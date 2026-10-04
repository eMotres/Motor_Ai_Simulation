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
                   (n_wires = num_wires_per_slot × wire_split: a split wire row
                    is N strips of wire_width, each its own conductor; the
                    current in one of them carries the ÷N when the strips are
                    parallel — see winding.n_parallel_effective)

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
from typing import Any, Dict, List, Mapping, Tuple, Optional, Literal

import numpy as np

from motor_ai_sim.simulation.pardiso_lifetime import (
    own_pardiso as _own_pardiso, pardiso_scope as _pardiso_scope,
)


def mesh_feature_floor_mm(geo: Mapping[str, Any], min_size_mm: float,
                          hi_fidelity: bool = False) -> Optional[float]:
    """Coarsest iron element the solver allows = smallest tooth/slot ÷2 (÷4 hi-fi),
    never below min_size_mm.  ONE definition shared by the transient solve and
    the Mesh-tab viewer / slider cap (routes.simulation), so the slider range
    and the solved mesh can never disagree.  None when no feature is known."""
    try:
        feat = min(float(geo.get("slot_width", 1e9) or 1e9),
                   float(geo.get("tooth_width", 1e9) or 1e9))
    except (TypeError, ValueError):
        return None
    if not (0.0 < feat < 1e8):
        return None
    return max(float(min_size_mm), feat / (4.0 if hi_fidelity else 2.0))

# ── Lower layers ─────────────────────────────────────────────────────────────
# Domain tags / mesh flags and the whole geometry->mesh stage now live in their
# own modules.  Re-exported here rather than merely imported: routes.simulation,
# modules.mesh and geo_mesh all reach for these off fem_solver_2d, and moving a
# file should not break a caller.
from motor_ai_sim.simulation.sb_domains import (  # noqa: F401  (re-export)
    DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_COIL, DOM_COIL_BASE, DOM_MAG_BASE,
    DOM_MAG_N, DOM_MAG_S, DOM_OUTER, DOM_ROTOR, DOM_SHAFT, DOM_SLEEVE,
    DOM_STATOR,
    _GMSH_LOCK, _N_SLIP, _SB_BAND_DELTA_FRAC, _SB_BELT, _SB_GEO_MESH,
    _SB_GEO_SECTOR, _SB_IRON_RESAMPLE, _SB_IRON_TEMPLATE, _SB_POLE_COPY_ROTOR,
    _SB_POLE_COPY_STATOR, _SB_ROT_PERIODICITY, _SB_STRUCTURED_GAP,
    _SB_STRUCTURED_STRIPS, _SG_EPS_OVERRIDE, _SG_M_TARGET,
    GAP_LAYERS_MIN as _GAP_LAYERS_MIN, effective_gap_layers as _effective_gap_layers,
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
from motor_ai_sim.simulation.dc_orbit import (
    DcOrbitSolve as _DcOrbitSolve,
    flux_shift_to_state as _flux_shift_to_state,
    handover_flux_offset as _handover_flux_offset,
    ll_flux as _ll_flux,
    ll_inductance as _ll_inductance,
)
# THE EXCITATION SOURCE INTERFACE.  Everything that decides WHAT this solver
# applies — waveform, settle schedule, report block — is behind it, so the
# five shipped drives are five implementations rather than a dozen if-branches,
# and an EXTERNAL controller / co-simulation can be handed in instead
# (fem_transient_sliding_band(..., excitation=my_source)).  The solver itself
# only ever switches on `kind` ('I' = imposed currents, a source term; 'V' =
# imposed voltages, currents as circuit unknowns), which is two different
# linear algebras rather than two settings.
from motor_ai_sim.simulation.excitation import (
    ExcitationSource as _ExcSource,
    Feedback as _Feedback,
    SineVoltageSource as _SineVoltageSource,
    _V_SETTLE_ENV as _SOURCE_V_SETTLE_ENV,
    make_source as _make_source,
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
from motor_ai_sim.simulation.rotor_window import (
    commensurate_rotor_window as _commensurate_rotor_window,
    commensurate_rotor_potential_window as _commensurate_rotor_potential_window,
    period_shift_map as _period_shift_map,
)
from motor_ai_sim.simulation.periodic_accel import (
    rre_extrapolate as _acc_rre,
    extrapolate_by_sector as _acc_rre_sec,
    restrict_shift as _acc_restrict,
    dc_error_correction as _acc_dc_eec,
    image_mean_start_refusal as _acc_img_refusal,
    period_map_residual as _acc_pm_resid,
    pole_pair_image_mean as _acc_image_mean,
    single_mode_extrapolate as _acc_single,
    shift_cycles as _acc_cycles,
    sector_components as _acc_comp,
    slow_tail_resid as _acc_tail,
    MIN_VERIFY_PERIODS as _acc_min_verify,
)
from motor_ai_sim.simulation.sb_postproc import (
    drop_settling_frames as _drop_settling_frames,
    retained_window_metadata as _retained_window_metadata,
    snapshot_scalar_history as _snapshot_scalar_history,
    eddy_settle_resid as _eddy_settle_resid,
    eddy_period_resid as _eddy_period_resid,
    EDDY_SIGNIFICANT_BODY_W as _EDDY_SIG_W,
    hybrid_torque as _hybrid_torque,
    space_vector_hybrid_torque as _space_vector_hybrid_torque,
    TORQUE_MEAN_SOURCE as _TORQUE_MEAN_SOURCE,
    torque_method_diagnostics as _torque_method_diagnostics,
    torque_harmonics as _torque_harmonics,
)
from motor_ai_sim.simulation.moving_band import slip_ring_nodes as _slip_ring_nodes
from motor_ai_sim.simulation.virtual_work_torque import (
    air_mask_from as _vwt_air_mask, frame_torques as _frame_torques,
    resolve_torque_method as _resolve_torque_method,
    prepare_sliding_band_frame_torques as _prepare_sb_frame_torques,
    coulomb_series_summary as _coulomb_series_summary,
)
from motor_ai_sim.simulation.p2_state_capture import (
    current_p2_state_capture as _current_p2_state_capture,
    emit_selected_p2_state as _emit_selected_p2_state,
)
from motor_ai_sim.simulation.field_ops import (  # noqa: F401  (re-export)
    MU0, RHO_CU_20, ALPHA_CU,
    _snap_steps_to_nodes, _build_magnet_bh_curve_payload, _b_from_bh_at_H,
    _mu_r_from_bh, _mu_r_from_bh_vec,
    _per_triangle_B, _triangle_areas, _maxwell_stress_torque,
    _arkkio_torque, _p2_B_at_quad, _arkkio_torque_p2,
    _prepare_arkkio_torque_p2,
    band_limit_torque, torque_metrics, end_winding_factor_geom, copper_loss_W,
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
    _stitch_full_half, build_trace as _mesh_build_trace,
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
# a 5-8 Nm p-p no-load torque floor (notes in the private data repository).
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
# q-axis (identical to commercial FEM's el_deg).  Cached per topology; θ* is invariant to
# dimension sweeps.
#
# PER WORKSPACE (2026-09-30, Codex review of PR #68).  This was one module-global
# dict for the whole API process, so one account's calibration answered for
# every other account that happened to build the same key.  It is now a
# `workspace.ws_map`, like `_SB_WARM_CACHE`, and its disk mirror
# (`_daxis_disk_path`) already sits in the workspace root.  With
# `WORKSPACES_ROOT` unset there is one workspace (the process one), so a
# single-user workstation sees the same cache it always had.
#
# The key carries NO material fingerprint, on purpose: θ* is a mirror-symmetry
# property of the cross-section, and an isotropic steel or magnet card cannot
# break that symmetry.  Measured solver-direct 2026-09-30 (production
# `_calibrate_daxis`, 24 frames, disk off; base N52UH_150C + B10AHV900M, then
# magnet -> F52SH_120C, then both cores -> 20SW1200):
#     40 mm 12s/14p:  60.0228 / 60.0071 / 60.0154 deg
#     L155  12s/10p: 120.0144 / 120.0121 / 120.0132 deg
# i.e. mesh noise, the same spread as three cross-sections of one family, and
# worth < 0.01 % of torque (dT/dγ at the rated γ: -0.26 %/deg on the 40 mm,
# -0.41 %/deg on L155).  Keying on materials would re-run a 24-frame
# calibration after every material edit and buy nothing.  ψ_PM, which DOES
# scale with the cards (+1.3 % / +4.2 % on the 40 mm, +3.2 % / +1.3 % on L155
# for the same two swaps), is keyed on them: see `psipm_cache_key`.
from motor_ai_sim import workspace as _WSD
#: Entries are one float each; the cap is a safety valve against a sweep that
#: builds thousands of cross-sections in one account.  An evicted entry is still
#: on disk and is read back without a solve.
_DAXIS_CACHE_MAX = 512
_DAXIS_CACHE = _WSD.ws_map("fem_solver_2d.daxis_cache", _DAXIS_CACHE_MAX,
                           lru_on_read=True)
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
# The lock is per workspace, like the cache and the disk file it guards: a
# calibration in one account no longer queues another account's.
_DAXIS_TLS = threading.local()
_DAXIS_LOCK = _WSD.ws_lock("fem_solver_2d.daxis_lock")


# ── Optimizer-candidate evaluation scope (owner 2026-09-24, fixes A + B) ─────
# An optimizer candidate (refine_proc child of a sweep / descent / CMA / auto /
# screen run) is scored on T, η, ripple and the loss split.  Two internal probes
# it used to pay for do not feed any of those:
#   A. the no-load ψ_PM probe in the Simulation summary — it only feeds the
#      chord Ld/Lq and the saturation-droop row, and refine_proc never reads
#      either (11–13 s per candidate on 68de0ca, measured
#      docs/OPTIMIZER_SPEED_REVIEW_2026-09-24.md);
#   B. a 24-frame d-axis calibration per new geometry.  θ* is a SYMMETRY
#      property: the rotor pole and the stator phase belt are each mirror-
#      symmetric about their own axis, so ψ_A(θ) is even about θ* and a change
#      of any dimension that keeps both mirrors cannot move its peak.  Measured
#      spread over 12 candidates: ±0.04° el (solver noise); 23–30 s per
#      candidate.  Candidates therefore reuse the run's BASELINE (the config
#      machine's) calibrated d-axis, unless their geometry differs from the
#      baseline in a key outside `_DAXIS_SYMMETRIC_KEYS` — a pole/slot count,
#      a segment form, or any key this list does not know (a future skew or
#      pole offset) — in which case that candidate calibrates its own.
# A ContextVar, not a module flag: the API process runs Simulation solves on
# other threads, and they must never inherit a candidate's shortcuts.  Default
# off, so every caller that does not ask (Simulation, coupled, passport,
# reports, final validation) keeps both probes exactly as before.
import contextlib as _ctxlib
import contextvars as _ctxvars

_OPT_CANDIDATE: "_ctxvars.ContextVar[bool]" = _ctxvars.ContextVar(
    "motor_ai_sim_optimizer_candidate", default=False)


@_ctxlib.contextmanager
def optimizer_candidate_scope(active: bool = True):
    """Run the enclosed solve as an optimizer CANDIDATE (see above)."""
    _tok = _OPT_CANDIDATE.set(bool(active))
    try:
        yield
    finally:
        _OPT_CANDIDATE.reset(_tok)


#: The demag method an OPTIMIZER CANDIDATE evaluation asks for (owner
#: 2026-10-04): the adaptive two-window shortcut for candidates, the full
#: pre-pass for everything reported and for the final verification.  Set ONLY
#: by `refine_proc.run_one` for ``sampling_purpose="optimization"`` evals
#: (`tdm_demag_scope`); carried into a solve-pool child like the candidate flag.
#: Never read from the environment.  None = the full pre-pass.
_TDM_DEMAG_REQUEST: "_ctxvars.ContextVar[Optional[str]]" = _ctxvars.ContextVar(
    "motor_ai_sim_tdm_demag_request", default=None)


@_ctxlib.contextmanager
def tdm_demag_scope(mode: Optional[str]):
    """Run the enclosed solves with TDM demag ``mode`` ("shortcut" for an
    optimizer candidate, None = full) unless a solve names its own."""
    if mode not in (None, "full", "shortcut"):
        raise ValueError("tdm_demag must be 'full' or 'shortcut', got %r" % (mode,))
    _tok = _TDM_DEMAG_REQUEST.set(mode)
    try:
        yield
    finally:
        _TDM_DEMAG_REQUEST.reset(_tok)


def optimizer_candidate_active() -> bool:
    """True inside ``optimizer_candidate_scope(True)`` on THIS context only."""
    return bool(_OPT_CANDIDATE.get())


#: Geometry keys whose change keeps BOTH mirror symmetries (rotor pole about the
#: d-axis, stator slot/phase belt about the phase axis), so they cannot move θ*.
#: Checked against cadquery_geometry: every magnet / pocket / tooth / slot
#: feature is drawn as ±x about its own axis (e.g. `_create_magnets` p1..p6),
#: and there is no skew, pole-offset or asymmetric-pole parameter.  Anything
#: NOT listed — the counts (num_poles, num_slots, num_seg, *_per_segment), the
#: angles/pitches derived from them, magnet_lamination_tan (splits magnets in
#: plane) or any key added later — forces the candidate to calibrate its own
#: axis.  A whitelist, deliberately: an unknown key is presumed to move it.
_DAXIS_SYMMETRIC_KEYS = frozenset({
    # stator
    "stator_diameter", "stator_outer_radius", "stator_inner_radius",
    "core_thickness", "slot_height", "slot_hs", "slot_width", "tooth_width",
    "tooth2_width", "cut_width", "stator_fillet_r", "stator_fillet_r1",
    "air_gap", "insulation_thickness", "wire_width", "wire_height",
    "wire_spacing_x", "wire_spacing_y", "num_wires_per_slot", "wire_parallel",
    "wire_split",
    # rotor
    "rotor_outer_radius", "rotor_inner_radius", "rotor_house_height",
    "rotor_hole", "rotor_fill_r", "magnet_height", "magnet_down_height",
    "magnet_fill_down", "magnet_fill_up", "magnet_fill_radius",
    "magnet_up_gap", "magnet_lamination", "shaft_height", "shaft_diameter",
    "sleeve_thickness",
    # 2-D solve: the stack length never enters the cross-section
    "motor_length",
})


def daxis_reuse_blockers(base_geo, cand_geo) -> List[str]:
    """Keys in which ``cand_geo`` differs from ``base_geo`` that COULD move θ*.

    Both dicts are MERGED geometries (``merge_geo_override``).  Empty list =
    the candidate may reuse the baseline's calibrated d-axis.
    """
    def _same(a, b) -> bool:
        if isinstance(a, bool) or isinstance(b, bool):
            return a == b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            a, b = float(a), float(b)
            return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
        return a == b
    base_geo, cand_geo = dict(base_geo or {}), dict(cand_geo or {})
    return sorted(k for k in set(base_geo) | set(cand_geo)
                  if not _same(base_geo.get(k), cand_geo.get(k))
                  and k not in _DAXIS_SYMMETRIC_KEYS)


def _baseline_daxis_for_candidate(geo, wind, n_sectors, progress_cb=None):
    """The run's BASELINE d-axis for an optimizer candidate, or ``(None, info)``.

    Baseline = the config machine this candidate is an override of (merged the
    same way the solver merges it), calibrated — or read from the memory/disk
    cache the Simulation tab and earlier candidates fill — under ITS OWN key, so
    the value is exactly what a Simulation run of the baseline uses.  ``info``
    says what was decided and why; it rides in the result.
    """
    from motor_ai_sim.config import get_config as _gc_b
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _mgo_b
    base_geo = _mgo_b(dict(_gc_b().get("geometry", {}) or {}), None)
    blockers = daxis_reuse_blockers(base_geo, geo)
    if blockers:
        return None, {"daxis_calibration": "own",
                      "daxis_reason": "geometry differs from the baseline in "
                                      + ", ".join(blockers)
                                      + " (can move the d-axis)"}
    p_b = _params_from_geo_dict(base_geo)
    daxis = _resolve_daxis_shift(p_b, base_geo, wind, int(p_b.num_poles) // 2,
                                 None, n_sectors, progress_cb=progress_cb)
    return float(daxis), {"daxis_calibration": "baseline",
                          "daxis_reason": "dimension-only change (both mirror "
                                          "symmetries kept) — baseline d-axis "
                                          "reused"}


def _winding_cache_identity(geo, wind, num_poles=None) -> str:
    """Versioned identity of the phase/sign basis actually used by the solver.

    Resolve the explicit layout with the same generator/parser as MotorDomains2D:
    spelling/separators and an explicit copy of the automatic winding are not
    physical differences. Inputs are request-local; never read global config.
    The version deliberately excludes legacy cache entries lacking this basis.
    """
    import hashlib
    import json
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    geo, wind = geo or {}, wind or {}
    slots = int(geo.get("num_slots") or round(float(geo.get("num_seg") or 0)
                * float(geo.get("num_slots_per_segment") or 0)))
    poles = int(num_poles or geo.get("num_poles") or round(float(geo.get("num_seg") or 0)
                * float(geo.get("num_poles_per_segment") or 0)))
    if slots <= 0 or poles <= 0:
        raise ValueError("Winding cache identity requires resolved positive slot/pole counts.")
    layout = build_winding_layout(slots, poles // 2,
                                  single_layer=int(wind.get("layers", 1)) == 1,
                                  layout_str=wind.get("layout") or None)
    payload = json.dumps(layout, separators=(",", ":"), ensure_ascii=True)
    return "w2-" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def psipm_cache_key(geo, wind, connection=None, materials=None) -> str:
    """The disk key ``noload_psi_pm`` files its answer under.

    Its own function so the "this cache cannot answer for another winding"
    property is testable without solving anything.  Like the winding identity
    it is a pure function of its arguments: the MATERIAL identity comes in as
    ``materials``, the string ``_noload_material_fingerprint()`` returns for
    the call being answered (both solving callers pass it).  ``None`` files
    under a marker no fingerprint can equal.

    ψ_PM DOES depend on the CONNECTION — not through the current (there is none)
    but through n_series: the phase flux linkage is the SUM of the series coil
    groups, so 4S links four times what 4P links.  The first version of this key
    argued "I = 0, the connection cannot matter" and cached one number for all
    connections; a 4S run would then have divided its 4S-scaled ψd against a 4P
    ψ_PM and shipped a silently wrong Ld.  (Caught by the user asking "did you
    account for the Winding Connection?" — reviewed, measured, fixed before it produced a
    number.)

    It depends on the WINDING SCALE for exactly the same reason: ψ_PM is the
    flux per turn times the series turns, divided by the parallel paths the
    phase splits over.  ``_daxis_geo_fingerprint`` cannot see any of that —
    ``_DAXIS_INERT_KEYS`` drops num_wires_per_slot, wire_parallel and
    wire_split because none of them can move the ANGLE θ*, which is
    what that fingerprint is for.  Keying ψ_PM on it alone therefore served a
    k = 1 machine's ψ_PM to a k = 3 one, and (since the split became geometry on
    2026-09-08, its strips SERIES turns) an unsplit machine's ψ_PM to a split
    one — a factor N in ψ_PM and N² in the Ld derived from it.  The two
    numbers that scale ψ_PM ride in the key
    instead.  Never fatal: an unreadable knob (a hand-edited yaml) falls back to
    a marker that simply never matches a cached entry.
    """
    _fp = _daxis_geo_fingerprint(geo)
    _conn_pm = str(connection or (wind or {}).get("connection") or "")
    try:
        from motor_ai_sim.winding import (turns_per_coil as _tpc_pm,
                                          n_parallel_effective as _npe_pm)
        _scale = "T%dP%d" % (_tpc_pm(geo),
                             _npe_pm((wind or {}).get("n_parallel", 1), geo))
    except Exception:       # noqa: BLE001 — a cache key may not raise
        _scale = "Tx"
    # v3: the MATERIAL CARDS ride in the key (2026-09-30).  ψ_PM is the magnet's
    # flux through the iron, so it scales with the magnet's Br and with how hard
    # the steel saturates; v2 keyed on geometry and winding only and served one
    # card set's ψ_PM (and, through the "ldq0_v1_" prefix, its catalogue Ld/Lq)
    # to the same cross-section with another.  Measured 2026-09-30, L155 at its
    # rated point: N52UH_150C -> F52SH_120C moves ψ_PM +3.2 % and
    # B10AHV900M -> 20SW1200 +1.3 %, and the chord Ld = (ψd − ψ_PM)/i_d
    # divides that error by a small i_d — a stale ψ_PM put Ld 29 % and 17 %
    # low (40 mm 12s/14p: 28 % and ~75 %).
    # v2 entries are simply never matched again (a miss, not a wrong answer).
    return "psipm_v3_%s_L%s_C%s_%s_%s_%s" % (
        _fp, int((wind or {}).get("layers", 1) or 1), _conn_pm or "cfg",
        _winding_cache_identity(geo, wind), _scale,
        str(materials) if materials else "m-unspecified")


#: The parts whose cards the NO-LOAD field sees: the magnets that drive it and
#: every solid that can carry or shunt it.  Copper, the slot liner and the wire
#: enamel carry no current at no load and are μ = 1, so editing them must not
#: throw away a cached ψ_PM.
_NOLOAD_MATERIAL_PARTS = ("stator_core", "rotor_core", "magnet", "shaft", "sleeve")


def _noload_material_fingerprint() -> str:
    """Identity of the material cards a no-load solve of THIS call would use.

    The same precedence the solver applies (`build_materials` and the σ
    block of `fem_transient_sliding_band`): the config's assignment, the
    per-request override's assignment on top, and each name resolved to its
    PROPS — the override-carried props first, else the library (which already
    includes the admin global layer).  Props, not names: a user who edits the
    B-H curve of a card keeps its name.  The excluded-part set is included
    because an EXCLUDED shaft is air whatever its card says.

    Never raises: an unresolvable card falls back to its name, which still
    separates two assignments from each other.
    """
    import hashlib as _hl
    import json as _jl
    try:
        from motor_ai_sim.config import get_material_assignments as _gma_fp
        asg = dict(_gma_fp() or {})
    except Exception:       # noqa: BLE001 — no config is the default machine
        asg = {}
    try:
        from motor_ai_sim.material_context import get_request_materials as _grm_fp
        ov = _grm_fp() or {}
    except Exception:       # noqa: BLE001
        ov = {}
    asg.update({k: v for k, v in (ov.get("assignment") or {}).items() if v})
    ov_props = ov.get("materials") or {}
    cards = {}
    for part in _NOLOAD_MATERIAL_PARTS:
        name = asg.get(part)
        if not name:
            continue
        name = str(name)
        props = None
        if name in ov_props:
            props = ov_props[name]
        else:
            try:
                from motor_ai_sim import materials as _ml_fp
                obj = _ml_fp.resolve_assigned(part, name)
                import dataclasses as _dc_fp
                props = (_dc_fp.asdict(obj) if _dc_fp.is_dataclass(obj)
                         else dict(vars(obj)))
            except Exception:   # noqa: BLE001 — the name alone still separates
                props = None
        cards[part] = [name, props]
    try:
        cards["__excluded__"] = sorted(_excluded_parts())
    except Exception:       # noqa: BLE001
        pass
    try:
        blob = _jl.dumps(cards, sort_keys=True, default=str)
    except Exception:       # noqa: BLE001
        blob = repr(sorted((k, str(v[0]) if isinstance(v, list) else str(v))
                           for k, v in cards.items()))
    return "m" + _hl.md5(blob.encode()).hexdigest()[:12]


def _calibration_sector_count(num_slots, num_poles, wind):
    """Largest certified sector for a private probe, without changing the run."""
    from motor_ai_sim.simulation.geometry_2d import (
        build_winding_layout, validate_sector_symmetry)
    slots, poles = int(num_slots), int(num_poles)
    layout = build_winding_layout(slots, poles // 2,
                                  single_layer=int((wind or {}).get("layers", 1)) == 1,
                                  layout_str=(wind or {}).get("layout") or None)
    symmetry = math.gcd(slots, poles)
    for sectors in range(symmetry, 1, -1):
        if symmetry % sectors:
            continue
        try:
            validate_sector_symmetry(slots, poles, sectors, layout, paired_stator=True)
        except ValueError:
            continue
        return sectors
    return 1


#: How many rotor positions of the reported window the frozen-permeability
#: inductance probe is evaluated at.  The incremental L(θ) is modulated by the
#: slot harmonics, so ONE position is not the machine's inductance; four evenly
#: spaced positions average that modulation out and — because the four are
#: kept — also MEASURE it (``spread_pct`` below).  Four extra linear back-solves
#: on a matrix the frame already assembled is ~1 s on the 200 mm mesh, against a
#: run of minutes.
_INC_LDQ_SAMPLES = 4


def frozen_permeability_ldq(p2, Pro, free, A2, f_mag, Pa, Pb, psi_of, th_dq,
                            n_parallel=1):
    """Incremental d-q inductances at ONE rotor position by FROZEN PERMEABILITY.

    THE EQUATIONS.  A converged nonlinear magnetostatic frame satisfies

        K(ν(|B|))·A  =  f_PM + Σ_k i_k·f_k                              (1)

    with ν the per-element reluctivity the B-H curve put there and f_k the
    unit-current source column of phase k.  FREEZE that reluctivity — keep the
    operator K* ≡ K(ν*) of the loaded iron and stop letting ν follow B — and
    (1) is LINEAR, so superposition splits the field exactly:

        A  =  A_PM*  +  Σ_k i_k·x_k ,   K*·A_PM* = f_PM ,  K*·x_k = f_k  (2)

    Park both sides at this rotor position and the flux linkages split the same
    way, ψ_dq = ψ*_PM,dq + L·i_dq, with

        L  =  [[∂ψd/∂i_d, ∂ψd/∂i_q],
               [∂ψq/∂i_d, ∂ψq/∂i_q]]                                    (3)

    read off two solves of K* with a UNIT d- and a unit q-axis current.  The
    size of the perturbation does not enter: the frozen operator is linear, so
    Δψ/Δi is exact for any Δi, and solving (i + Δi) against (i) with the
    magnets on returns these same numbers — the magnet term cancels in the
    difference.  L is symmetric (reciprocity of a linear magnetic circuit);
    ``reciprocity_pct`` reports the measured asymmetry instead of averaging it
    away.  On linear iron ν* is ν, (2) is exact for the machine itself, and the
    incremental values EQUAL the chord ones.

    WHY IT REPLACES THE CHORD.  ``(ψd − ψ_PM)/i_d`` divides by a ψ_PM measured
    at NO LOAD, while the loaded iron carries a magnet flux ψ*_PM the armature
    reaction has MOVED by several per cent — sagged 6.1 % on the L180 rated
    duty, where the reaction saturates the flux path; RAISED 4.7 % on the L155,
    where it saturates a leakage path instead.  That difference lands whole in
    the numerator,
    and where i_d is a small share of the current it DOMINATES it: the L180
    generator report a client reviewed read Ld 0.0976 mH against Lq 0.0693 for
    a machine whose real saliency is Lq/Ld = 1.05.  The third solve here — K*
    with the magnet source alone — measures ψ*_PM instead, on BOTH axes: under
    cross-saturation it acquires a q component (−7.5 mWb on that duty), which
    is why the chord ψq/i_q is not Lq either.

    ``Pa``/``Pb`` are the unit-i_A and unit-i_B source columns with i_C folded
    in (i_C = −i_A − i_B), the same two the voltage drive's phasor initialiser
    uses.  ``psi_of`` maps a nodal field to the three PHASE flux linkages, and
    the abc currents handed to the sources are PER BRANCH — hence the
    ``n_parallel`` division, which is what makes the returned matrix the
    machine's per-phase inductance rather than n_parallel times it.

    Returns a dict in HENRY and WEBER (the caller scales and rounds).
    """
    import numpy as _np
    K, _ = p2.Kpw(A2)          # pointwise secant ν of THIS converged field
    Kff = (Pro.T @ K @ Pro).tocsr()[free][:, free].tocsc()
    _npf = float(max(1, int(n_parallel)))
    _iA_d, _iB_d, _ = _ipark_dq(1.0 / _npf, 0.0, th_dq)
    _iA_q, _iB_q, _ = _ipark_dq(0.0, 1.0 / _npf, th_dq)

    def _red(v):
        return _np.asarray(Pro.T @ v).ravel()[free]

    X = p2.solve_ff(Kff, _np.column_stack([
        _red(f_mag),                            # the magnets in the LOADED iron
        _red(_iA_d * Pa + _iB_d * Pb),          # unit d-axis phase current
        _red(_iA_q * Pa + _iB_q * Pb),          # unit q-axis phase current
    ]), spd=True)
    _pm = psi_of(p2.pad2(Pro, free, X[:, 0]))
    _xd = psi_of(p2.pad2(Pro, free, X[:, 1]))
    _xq = psi_of(p2.pad2(Pro, free, X[:, 2]))
    _pmd, _pmq = _park_dq(_pm[0], _pm[1], _pm[2], th_dq)
    _Ldd, _Lqd = _park_dq(_xd[0], _xd[1], _xd[2], th_dq)
    _Ldq, _Lqq = _park_dq(_xq[0], _xq[1], _xq[2], th_dq)
    _ref = max(abs(_Ldd), abs(_Lqq), 1e-30)
    return {
        "Ld_H": float(_Ldd), "Lq_H": float(_Lqq),
        "Ldq_H": float(0.5 * (_Ldq + _Lqd)),
        "reciprocity_pct": float(100.0 * abs(_Ldq - _Lqd) / _ref),
        "psi_d_pm_frozen_Wb": float(_pmd),
        "psi_q_pm_frozen_Wb": float(_pmq),
    }


def frozen_permeability_vsd(p2, Pro, free, A2, f_set, sc_psi, th_dq,
                            n_branch_per_set):
    """Six-phase (two 3-phase sets) inductances at ONE rotor position.

    Same frozen-permeability operator K* as :func:`frozen_permeability_ldq`
    (the ν of THIS converged loaded frame, held fixed), six back-solves — a
    unit PER-SET PHASE current in each of the six phases — give the full 6x6
    incremental inductance matrix L6 [H] (per-set phase current → per-set
    phase flux linkage).  It is then projected on the ORTHONORMAL
    vector-space decomposition ``inverter.ripple`` uses:

      d, q  the air-gap plane (both sets driven as the machine is driven,
            at the Park angle of this frame);
      x-y   the orthogonal complement of d, q and each set's zero sequence —
            for the in-phase sets that is "set 1 +i, set 2 -i" (the
            differential, circulating mode).

    Every value is PER SET PHASE, the same reference the ripple model's
    bridge-phase L_d/L_q take (a set = half the paths: L_d,set = 2 L_d of the
    3-phase machine).  No filter and no fit: L_xy is Xᵀ·L6·X on the
    plane, its two principal values reported beside their mean.

    ``f_set[s][ph]`` are the unit-BRANCH sources; a unit set-phase current is
    ``1/n_branch_per_set`` in each branch; ``sc_psi`` turns A·f into one
    branch's flux linkage (stack · sectors / n_branch_per_set).
    """
    import numpy as _np
    from motor_ai_sim.winding_sets import vsd_basis as _vb
    K, _ = p2.Kpw(A2)
    Kff = (Pro.T @ K @ Pro).tocsr()[free][:, free].tocsc()
    _keys = [(s, ph) for s in (1, 2) for ph in 'ABC']
    _nb = float(max(1.0, n_branch_per_set))

    def _red(v):
        return _np.asarray(Pro.T @ v).ravel()[free]
    X = p2.solve_ff(Kff, _np.column_stack(
        [_red(f_set[s][ph] / _nb) for s, ph in _keys]), spd=True)
    L6 = _np.zeros((6, 6))
    for j in range(6):
        A = p2.pad2(Pro, free, X[:, j])
        for i, (s, ph) in enumerate(_keys):
            L6[i, j] = sc_psi * float(A @ f_set[s][ph])
    B = _vb(th_dq, _ipark_dq)
    ed, eq, XY = B["d"], B["q"], B["xy"]
    Lxy2 = XY.T @ L6 @ XY
    Lxy2 = 0.5 * (Lxy2 + Lxy2.T)
    ev = _np.linalg.eigvalsh(Lxy2)
    _ref = max(float(_np.max(_np.abs(L6))), 1e-30)
    return {
        "Ld_set_H": float(ed @ L6 @ ed), "Lq_set_H": float(eq @ L6 @ eq),
        "Lxy_H": float(0.5 * (ev[0] + ev[1])),
        "Lxy_min_H": float(ev[0]), "Lxy_max_H": float(ev[1]),
        # coupling of the air-gap plane into x-y: ~0 for identical sets
        "dq_xy_coupling_pct": float(100.0 * _np.max(_np.abs(
            _np.vstack([ed, eq]) @ L6 @ XY)) / _ref),
        "reciprocity_pct": float(100.0 * _np.max(_np.abs(L6 - L6.T)) / _ref),
    }


def noload_psi_pm(geo, wind, pole_pairs, n_sectors, daxis_deg,
                 geo_override=None, progress_cb=None, connection=None):
    """Magnet flux linkage ψ_PM [Wb, phase] — the d-axis flux at I = 0.

    One cheap no-load transient (6 frames on the calibration mesh), parked into
    the same d-q frame the loaded solve uses, cached per geometry next to the
    d-axis angle: ψ_PM is what separates Ld from the PM contribution in
    ψd = ψ_PM + Ld·id, and without it a single load point cannot yield Ld.

    Returns (psi_pm_Wb, psi_q0_Wb).  ψq at no load is a FRAME CHECK, not a
    result — magnet flux lives entirely on d, so a ψq0 that is not small next
    to ψ_PM means the transform angle is wrong, and the caller must refuse to
    turn it into an inductance.
    """
    import math as _m, json as _j, os as _os
    key = psipm_cache_key(geo, wind, connection,
                          materials=_noload_material_fingerprint())
    _dp = _daxis_disk_path()
    if _dp and _os.path.exists(_dp):
        try:
            with open(_dp) as _f:
                _v = _j.load(_f).get(key)
            if isinstance(_v, (list, tuple)) and len(_v) == 2:
                return float(_v[0]), float(_v[1])
        except Exception:
            pass
    _cal_ns = _calibration_sector_count(int((geo or {}).get("num_slots") or 1),
                                      2 * int(pole_pairs), wind)
    _cal_D = float((geo or {}).get("stator_diameter") or 0.0) or 40.0
    _cal_mesh = round(min(2.0, max(0.5, 1.4 * _cal_D / 150.0)), 2)
    # The connection the loaded run used — the SAME spelling `psipm_cache_key`
    # keys on.  This local was lost when the key moved into its own function
    # (2026-09-08) and every live run's bench Ld/Lq probe died on a NameError
    # ("the summary will carry none") from 13:23 to 22:30 that day.
    _conn_pm = str(connection or (wind or {}).get("connection") or "")
    cal = em_transient_eval(
        n_steps_per_period=6, n_periods=1.0, gamma_deg=0.0, I_phase_rms=0.0,
        mesh_size_mm=_cal_mesh, min_size_mm=0.3, outer_air_factor=1.2,
        gap_layers=2.0, n_sectors=_cal_ns if _cal_ns >= 2 else -1,
        coil_temp_c=120.0, rotor_eddy=False, iron_template=True,
        structured_gap=True, geo_mesh=True, geo_override=geo_override,
        element_order=2, daxis_deg=float(daxis_deg), progress_cb=progress_cb,
        # ψ sampler, not a cogging run: 6 frames, never raised (8997bb3 made
        # this probe 96/120 frames — +50–70 s per new geometry).
        sampling_purpose="internal_probe",
        # The same connection the LOADED run used — psi scales with n_series.
        **({} if not _conn_pm else {"connection": _conn_pm}))
    if not cal.get("picard_converged", False):
        raise RuntimeError("no-load psi_PM solve did not converge — ψ_PM off an "
                           "unconverged field is not a measurement")
    from motor_ai_sim.simulation.drive import park as _park
    ang = cal.get("rotor_angle_deg") or []
    pa, pb, pc = cal.get("psi_A_Wb"), cal.get("psi_B_Wb"), cal.get("psi_C_Wb")
    ds, qs = [], []
    for k, a in enumerate(ang):
        th = _m.radians(a * float(pole_pairs) + float(daxis_deg) - 90.0)
        d, q = _park(pa[k], pb[k], pc[k], th)
        ds.append(d); qs.append(q)
    import numpy as _np
    out = (float(_np.mean(ds)), float(_np.mean(qs)))
    if _dp:
        try:
            _disk = {}
            if _os.path.exists(_dp):
                try:
                    with open(_dp) as _f:
                        _disk = _j.load(_f) or {}
                except Exception:
                    _disk = {}
            _disk[key] = [out[0], out[1]]
            with open(_dp, "w") as _f:
                _j.dump(_disk, _f)
        except Exception:
            pass
    return out


#: The temperature a catalogue quotes its constants at.  Kept here beside the
#: probe that measures them so the solver and ``routes/coupled`` cannot disagree
#: about what "cold" means (coupled imports its own ``COLD_CONSTANTS_C``; the
#: test asserts the two are the same number).
NOLOAD_LDQ_TEMP_C = 20.0


def noload_incremental_ldq(geo, wind, pole_pairs, daxis_deg,
                           geo_override=None, connection=None,
                           magnet_temp_c=NOLOAD_LDQ_TEMP_C,
                           coil_temp_c=NOLOAD_LDQ_TEMP_C,
                           progress_cb=None):
    """CATALOGUE Ld / Lq: incremental, at zero current, at 20 °C.

    The value a datasheet prints next to KV.  KV is quoted at no load and at a
    stated temperature because that is the one state every machine can be
    compared in; the inductances a control engineer sizes a loop with are
    quoted the same way, and the owner's rule (2026-09-20) is that this
    document does too — *"Ld/Lq also need to be given at 20 degrees and at
    zero current, like KV"*.

    ONE cheap no-load transient (the ψ_PM calibration knobs: 6 frames on the
    calibration mesh) with the magnets and the winding at ``magnet_temp_c`` /
    ``coil_temp_c``, and the frozen-permeability probe of
    ``frozen_permeability_ldq`` run on its frames.  "Zero current" is not
    "unsaturated iron": at no load the magnets have already pushed the teeth up
    the B-H curve, and the ν that is frozen here is that state's — which is
    exactly the iron a small-signal bench measurement meets.

    Cached on disk next to ψ_PM and the d-axis angle, keyed on the same
    geometry/winding identity plus the magnet temperature (a colder magnet
    saturates the teeth harder, so the answer moves with it).  Returns the
    ``inc_ldq`` block of that run, or ``None`` when it could not be measured.
    """
    import json as _j, os as _os
    key = "ldq0_v1_%s_M%g" % (psipm_cache_key(
                                  geo, wind, connection,
                                  materials=_noload_material_fingerprint()),
                              float(magnet_temp_c))
    _dp = _daxis_disk_path()
    if _dp and _os.path.exists(_dp):
        try:
            with open(_dp) as _f:
                _v = _j.load(_f).get(key)
            if isinstance(_v, dict) and _v:
                return dict(_v)
        except Exception:       # noqa: BLE001 — a cache miss is not an error
            pass
    _cal_ns = _calibration_sector_count(int((geo or {}).get("num_slots") or 1),
                                        2 * int(pole_pairs), wind)
    _cal_D = float((geo or {}).get("stator_diameter") or 0.0) or 40.0
    _cal_mesh = round(min(2.0, max(0.5, 1.4 * _cal_D / 150.0)), 2)
    _conn = str(connection or (wind or {}).get("connection") or "")
    cal = em_transient_eval(
        n_steps_per_period=6, n_periods=1.0, gamma_deg=0.0, I_phase_rms=0.0,
        mesh_size_mm=_cal_mesh, min_size_mm=0.3, outer_air_factor=1.2,
        gap_layers=2.0, n_sectors=_cal_ns if _cal_ns >= 2 else -1,
        coil_temp_c=float(coil_temp_c), magnet_temp_c=float(magnet_temp_c),
        rotor_eddy=False, iron_template=True, structured_gap=True,
        geo_mesh=True, geo_override=geo_override, element_order=2,
        daxis_deg=float(daxis_deg), progress_cb=progress_cb, inc_ldq=True,
        sampling_purpose="internal_probe",      # Ld/Lq probe: never raised
        **({} if not _conn else {"connection": _conn}))
    if not cal.get("picard_converged", False):
        raise RuntimeError("no-load Ld/Lq solve did not converge — an "
                           "inductance off an unconverged field is not a "
                           "measurement")
    out = cal.get("inc_ldq")
    if not isinstance(out, dict) or out.get("Ld_mH") is None:
        return None
    out = dict(out)
    out["magnet_temp_c"] = float(magnet_temp_c)
    out["coil_temp_c"] = float(coil_temp_c)
    out["method"] = ("frozen-permeability incremental at i = 0, %g °C — the "
                     "catalogue convention, quoted like KV"
                     % float(magnet_temp_c))
    if _dp:
        try:
            _disk = {}
            if _os.path.exists(_dp):
                try:
                    with open(_dp) as _f:
                        _disk = _j.load(_f) or {}
                except Exception:
                    _disk = {}
            _disk[key] = dict(out)
            with open(_dp, "w") as _f:
                _j.dump(_disk, _f)
        except Exception:       # noqa: BLE001 — a cache write may not fail a run
            pass
    return out


def _daxis_disk_path():
    """Shared on-disk DAXIS cache so sweep subprocesses (fresh interpreters, empty
    in-memory cache) don't each re-run the calibration → per-design timeout."""
    try:
        import os
        from motor_ai_sim.workspace import root as _ws_root
        return os.path.join(str(_ws_root()), ".daxis_cache.json")
    except Exception:
        return None


def _daxis_disk_entry(path, skey):
    """The calibrated angle filed under ``skey`` in the disk mirror, or None.

    The file is PER WORKSPACE (``_daxis_disk_path`` resolves the caller's
    workspace root), and it is read as untrusted: an entry is served only when
    it is a finite number of degrees.  Anything else — a ψ_PM list or Ld/Lq
    dict that shares the file, a string, NaN/inf (Python's json writes and
    reads them), a file that is not a JSON object at all — is a miss, and the
    caller calibrates as if the file were absent.  Never raises.
    """
    import json
    import os
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path) as _f:
            disk = json.load(_f)
        if not isinstance(disk, dict):
            return None
        v = disk.get(skey)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        v = float(v)
        if not math.isfinite(v):
            return None
        return v % 360.0
    except Exception:       # noqa: BLE001 — a cache miss is not an error
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
        # Only what can MOVE the d-axis.  θ* is a symmetry property of the
        # magnetic cross-section — poles, slots, magnet placement, tooth/yoke
        # shape.  The keys below are dimensions the field never sees in that
        # role: the stack length (2-D solve), the shaft wall (inside the yoke;
        # aluminium in every machine here), the carbon sleeve (μ = 1, sits in
        # the mechanical clearance — the magnetic gap is the iron-to-bore
        # distance), the liner thickness and the wire sizes (they place the
        # conductors inside the slot; the phase axis is the slot's).  Keying on
        # them re-calibrated the axis — 24 no-load frames — after the user
        # thickened the shaft wall ("why recalibrate it, when I only
        # increased the shaft thickness?", 2026-09-07).
        _g = {k: v for k, v in dict(geo or {}).items()
              if k not in _DAXIS_INERT_KEYS}
        return _hl.md5(_jl.dumps(_g, sort_keys=True,
                                 default=str).encode()).hexdigest()[:16]
    except Exception:
        return "nofp"


#: Geometry keys that cannot move the calibrated d-axis (see the docstring of
#: `_daxis_geo_fingerprint`).  Deliberately conservative: anything that shapes
#: iron, magnets, slots or the magnetic gap stays IN the key.
#:
#: ``wire_split`` stays inert even though it is now REAL GEOMETRY (2026-09-08:
#: the strips are drawn, meshed and solved).  Re-checked rather than assumed:
#: θ* is the angle at which the NO-LOAD ψ_A peaks, and laying N strips side by
#: side does not touch the magnets, the rotor or the tooth axis — it subdivides
#: copper about the slot's own centre-line and widens the slot symmetrically,
#: exactly as a wider ``wire_width`` would (already inert here for that reason).
#: The no-load flux linkage PER TURN is unchanged, so ψ_A(θ) is the same curve
#: scaled and its peak sits at the same θ.
#:
#: The strips being SERIES turns does not change that: the flux PER TURN cannot
#: depend on how the turns are connected to each other, so the split only
#: rescales ψ (×N), and a constant factor on ψ_A(θ) cannot move the angle of its
#: peak.
#:
#: What the split does change — turns, ψ magnitude, R, L — is not the d-axis;
#: the ``_geometry_fingerprint`` and the physics cache keys DO see the key,
#: which is where a changed machine has to be caught.
_DAXIS_INERT_KEYS = frozenset({
    "motor_length", "shaft_height", "shaft_diameter", "sleeve_thickness",
    "insulation_thickness", "wire_width", "wire_height", "wire_spacing_x",
    "wire_spacing_y", "num_wires_per_slot", "wire_parallel", "wire_split",
})


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
    re-calibrates. The resolved phase/sign winding basis is also part of the
    topology: equal counts can orient phase A differently. Its versioned token
    prevents reusing older memory/disk entries that never identified the basis.
    """
    return (int(getattr(p, "num_poles", 0) or 0),
            int((geo or {}).get("num_slots") or 0),
            int((wind or {}).get("layers", 1) or 1),
            str((wind or {}).get("connection", "")),
            _winding_cache_identity(geo, wind, getattr(p, "num_poles", None)),
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
        _v = _daxis_disk_entry(_dp, skey)
        if _v is not None:
            _DAXIS_CACHE[key] = _v
            return _v
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
        _cal_ns = _calibration_sector_count(int((geo or {}).get("num_slots") or 1),
                                          p.num_poles, wind)
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
                if _done is None:
                    # the nested solve's SET-UP checkpoint: pass it through
                    # as one (keeps the bar), so a Stop reaches this stage too
                    progress_cb(None, None)
                    return
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
            geo_override=geo_override, element_order=2,
            # ψ sampler, not a cogging run: never raised by the cogging policy.
            sampling_purpose="internal_probe")
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
            if not isinstance(_disk, dict):
                _disk = {}            # not a cache this code wrote: start over
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
# ── THE SETTLE SCHEDULE NOW BELONGS TO THE SOURCE ────────────────────────────
# Settling PERIODS marched (and discarded) before the reported window on every
# imposed-VOLTAGE run — sinusoid or PWM — because the phase currents are circuit
# state with an L/R start-up transient.  Every knob that used to live here is a
# field of `excitation.SettlePolicy` now, read off `source.settle_policy()`:
#
#   periods_static       SB_V_SETTLE_PERIODS — source defaults: 10 for the
#                        sinusoid, 2 for PWM, 0 for imposed current. The P2
#                        solver selects 4 instead of 10 only for ordinary
#                        sinusoidal voltage without eddy/demag, after a pinned
#                        A/B run; an EXPLICIT env value always wins.
#   adaptive_tau_mult    SB_V_SETTLE_TAU_MULT — periods = ceil(k·τ_e/T_e) with
#   periods_cap          SB_V_SETTLE_MAX        τ_e = max(Ld,Lq)/R from the
#                        phasor initialiser, floored at periods_static, capped.
#   coarse_settle        SB_PWM_COARSE_SETTLE — mixed coarse/fine settle.
#   fine_settle_periods  SB_PWM_FINE_SETTLE   — WHOLE fine settling periods at
#                        the end of that prefix: the switching turn-on DC is
#                        solved out on the first, the last verifies (B5).
#   aitken               Δ² period-boundary flux anchors (the sinusoid only).
#   dc_orbit_solve       periodic-orbit solve of the DC mode (PWM) —
#   dc_orbit_correct     simulation/dc_orbit.py; SB_V_DC_SOLVE=0 leaves it
#                        measuring only, for a reference run.
#
# The MATH driven by those fields — _build_schedule, the adaptive block after
# the phasor initialiser, the Δ² anchor and the DC-orbit solve in the frame
# loop — lives here; only the decision of WHICH numbers to feed it moved.  See
# simulation/excitation.py.

# ── MIXED-RESOLUTION SETTLING ────────────────────────────────────────────────
# The settling periods exist to land the FUNDAMENTAL current orbit (and, when
# they are on, the demag Br ratchet and the eddy state).  None of that is
# switching-frequency business: the carrier ripple is a forced response with no
# history worth settling, and it is regenerated from scratch in a couple of
# carriers.  So a PWM run does not need to pay its fine step count for the
# settle — it can march the settle on the plain sinusoid fundamental (the very
# amplitude and angle the modulator is compensated to apply) at a COARSE step
# count on the SAME sliding-band mesh, then switch the real modulator on for
# the last `fine_settle_periods` settling periods and the reported window.
# Cost drops from steps x (settle+1) to coarse x (settle-2) + steps x 3.
#
# The ripple IS regenerated in a couple of carriers; what the old two-CARRIER
# pre-roll got wrong is the DC the turn-on leaves behind (about half the ripple
# amplitude), which decays with τ_e — ~27 electrical periods on the L155 — and
# therefore does not decay at all inside the reported window.  The fine part of
# the settle is now whole PERIODS, so the flux drift over one is an exact
# measurement of that DC and the DC-orbit solve takes it out at the end of the
# first; the last runs free and verifies.  (B5 / PWM study 2026-09-13; before
# it, this scheme's torque ripple read 55 % against the machine's own 0.9 %.)
#
# SB_PWM_COARSE_SETTLE: "0" forces the all-fine march, "1" forces the mixed
# scheme, unset -> AUTO: mixed only while the run is itself a coarse/partial
# pass (< 16 steps per switching period).  The env itself is read in
# simulation/excitation.py (SettlePolicy.coarse_settle /
# .coarse_settle_forced); the "< 16 steps per carrier" half of the AUTO rule
# needs the run's own step count and stays in _build_schedule below.
# Coarse settle resolution: the largest DIVISOR of the fine count that is <=
# this.  A divisor keeps every coarse step a whole number of fine steps and
# therefore a whole number of SLIP NODES — the rotor still lands on mesh nodes
# and the band is never rebuilt mid-run.  ~40 is the resolution the sinusoidal
# voltage drive is normally run at.
_PWM_COARSE_MAX = max(4, int(_os_sb.environ.get("SB_PWM_COARSE_MAX", "40") or 40))
# Acceptance for the DC left in the phase currents of the REPORTED period — the
# 2026-09-02 number, now measured the way it is written (worst phase, whole
# period; see the gauge at the end of the P2 branch).  Above it the reported
# torque ripple and current ripple are that DC and the payload says so.
_V_DC_RESIDUAL_TOL_A = 0.5
# SB_DEBUG_DUMP_FRAMES writes one file per SOLVED transient; this numbers them
# so the reference run cannot overwrite the run being studied.
_DUMP_SEQ = 0


def _period_dc(series, i0: int, i1: int, dt_w) -> float:
    """DC of ``series`` over the frames [i0, i1] — one whole electrical period.

    TRAPEZOIDAL, not a sample mean, and that is the whole accuracy of the thing.
    Sum the solved Crank–Nicolson circuit rows over a closed period and the flux
    telescopes away, leaving exactly

        R · Σ ½(i_k + i_{k−1})·Δt_k  =  Σ v_k·Δt_k  −  Δψ over the period

    so the ½(i_k + i_{k−1}) mean is 0 on a converged orbit IDENTICALLY — it is
    pinned by the equations that were solved, whatever the carrier does between
    samples.  A plain sample mean is not: at 1.4 steps per carrier (the 30 mm
    fixture) the aliased ripple walks into it and the (retired) DC anchor
    built on it over-corrected by 1.8x.  (B5 / PWM study 2026-09-13.)
    """
    x = np.asarray(series[max(i0 - 1, 0):i1 + 1], float)
    if x.size < (i1 - i0 + 2):          # no frame before the window: fall back
        x = np.concatenate([x[:1], x])  # to holding the first value
    w = np.asarray(dt_w, float)
    return float((0.5 * (x[1:] + x[:-1]) * w).sum() / max(w.sum(), 1e-30))
# (How much of the settle is marched FINE is SettlePolicy.fine_settle_periods —
# SB_PWM_FINE_SETTLE, read in simulation/excitation.py.  WHOLE periods, because
# the DC the modulator's turn-on leaves behind can only be measured over one.)

# ── CONDUCTING ROTOR UNDER AN IMPOSED VOLTAGE ────────────────────────────────
# The magnet/shaft σ·∂A/∂t dynamics used to be force-dropped on every imposed-
# voltage run, because the eddy path imposes each wire's current as an integral
# CONSTRAINT while the voltage circuit needs those same currents as UNKNOWNS.
# That conflict is resolved in p2_drive.ve_newton — the constraint VALUE became
# a function of the circuit state, I_b(i) = Iunit_b·i_phase(b), so the two
# borders merge into ONE (A, U, i_A, i_B) Newton — and the drop is gone: a
# voltage or PWM run now solves the conducting rotor and reports real magnet
# and shaft watts instead of a zero that meant "not solved".
# SB_VDRIVE_ROTOR_EDDY=0 restores the old force-drop (warning included) for
# anyone who needs to reproduce a pre-change number.
_SB_VDRIVE_ROTOR_EDDY = (
    _os_sb.environ.get("SB_VDRIVE_ROTOR_EDDY", "1") or "1").strip() != "0"


def _coarse_settle_nspp(fine_nspp: int, cap: int = None) -> int:
    """Largest divisor of ``fine_nspp`` that is <= ``cap`` (>= 4 if one exists).

    A DIVISOR, not merely a smaller number: the coarse step must be a whole
    number of fine steps so that it is also a whole number of slip nodes (the
    fine count already divides the ring).  Anything else would make the rotor
    land between nodes on the settle frames — the chaotic-torque failure the
    whole-node snap exists to prevent — or force a second mesh.
    """
    cap = _PWM_COARSE_MAX if cap is None else int(cap)
    n = max(1, int(fine_nspp))
    if n <= cap:
        return n
    best = 1
    for d in range(1, n + 1):
        if n % d == 0 and d <= cap and d > best:
            best = d
    return max(best, 1)

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

def _magnet_at_temp(mat_mag, magnet_temp_c: float, mag_name: str):
    """The assigned magnet card corrected to ``magnet_temp_c`` °C, and SAID SO.

    One line per solve, because a magnet quoted at 150 °C and the same magnet
    running at 163 °C are different physics — weaker AND closer to the knee —
    and a number that moved for that reason must be traceable to the reason.
    A card that cannot answer the request raises ``MagnetTemperatureError``
    (materials.py); it is not caught here, so the route can name it.
    """
    from motor_ai_sim.materials import MagnetTemperatureError  # noqa: F401
    hot = mat_mag.at_temperature(float(magnet_temp_c))
    try:
        _k0 = getattr(mat_mag, "h_knee", None)
        _k1 = getattr(hot, "h_knee", None)
        _knee = ("knee %.0f -> %.0f kA/m" % (_k0 * 1e-3, _k1 * 1e-3)
                 if (_k0 is not None and _k1 is not None) else "no knee on card")
        log.info("magnet %s rescaled from %g to %g degC: Br %.3f -> %.3f T, %s",
                 mag_name, float(getattr(mat_mag, "temperature_c", float("nan"))),
                 float(magnet_temp_c), float(mat_mag.Br), float(hot.Br), _knee)
    except Exception:      # noqa: BLE001 — a log line must never fail a solve
        pass
    return hot


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
    magnet_temp_c: Optional[float] = None,
) -> Dict[int, FEMMaterial]:
    """Build the per-domain material map for the FEM solve.

    Each magnet (DOM_MAG_BASE+i) and each coil (DOM_COIL_BASE+i) gets its
    own material entry with the polygon-specific source term.  Bulk
    materials (air, iron, etc.) share fixed ids.

    ``magnet_temp_c`` is THE magnet's temperature [°C].  ``None`` — every
    existing caller — uses the assigned magnet card exactly as the library
    states it, so the numbers are bit-identical to what they have always been.
    Given a number, the card is corrected to it
    (``materials.MagnetMaterial.at_temperature``): Br, the normal coercivity and
    the whole 2nd-quadrant demagnetisation curve — knee included — move
    together, which is what makes a magnet running at 163 °C weaker AND closer
    to its knee than the same magnet quoted at 150 °C.

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
    # ── Retaining sleeve ────────────────────────────────────────────────────
    # Carbon fibre is NON-MAGNETIC: mu_r = 1, so magnetically the ring IS the
    # air it displaces and the field is the field of the same machine without
    # it.  What it is not is a perfect insulator — sigma is small but finite, so
    # it dissipates, and that loss is solved rather than assumed.  The number
    # comes from the assigned material (default T800_UD_60), never from the
    # constant, which is the last resort when nothing resolves.
    _sleeve_name = (assignments.get("sleeve") or "").strip()
    if not _sleeve_name:
        try:
            from motor_ai_sim.materials import DEFAULT_PART_MATERIAL as _DPM
            _sleeve_name = _DPM.get("sleeve", "")
        except Exception:      # noqa: BLE001
            _sleeve_name = ""
    sleeve_sigma = SIGMA_SLEEVE
    if _sleeve_name:
        for _cat in ("insulator", "conductor", "steel"):
            try:
                _sm = _resolve_mat(_cat, _sleeve_name)
            except Exception:  # noqa: BLE001 — wrong category, keep looking
                continue
            sleeve_sigma = float(getattr(_sm, "sigma", 0.0) or 0.0)
            break
    # Magnet recoil μ_r, Br and BH curve (2nd-quadrant demag curve) from
    # the linked magnet material.
    mag_name = assignments.get("magnet")
    bh_magnet: Optional[List[Tuple[float, float]]] = None
    from motor_ai_sim.materials import MagnetTemperatureError
    if mag_name:
        try:
            mat_mag = _resolve_mat("magnet", mag_name)
            if magnet_temp_c is not None:
                mat_mag = _magnet_at_temp(mat_mag, magnet_temp_c, mag_name)
            Br      = float(getattr(mat_mag, "Br",     Br))
            mu_rec  = float(getattr(mat_mag, "mu_rec", 1.05))
            M_mag   = Br / MU0
            bh = getattr(mat_mag, "bh_curve", None)
            if bh and len(bh) >= 2:
                bh_magnet = [(float(h), float(b)) for (h, b) in bh]
        except MagnetTemperatureError:
            # A magnet that cannot be evaluated at the requested temperature is
            # not a resolution failure and must not be reported as one — the
            # card is fine, the REQUEST is unanswerable.  Let it out verbatim
            # (the route turns it into a 422 naming the card and the missing
            # coefficient) instead of burying it in "could not be resolved".
            raise
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
        DOM_SLEEVE: FEMMaterial("sleeve", mu_r=1.0, sigma=sleeve_sigma),
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

    # ── EXCLUDED parts are AIR ───────────────────────────────────────────────
    # Applied LAST so it covers the per-magnet (DOM_MAG_BASE+i) and per-coil
    # (DOM_COIL_BASE+i) entries built above as well as the bulk tags: an
    # excluded part must not keep a magnetisation, a source or a conductivity
    # by having been written after the switch.  mu_r = 1, sigma = 0, no B-H
    # curve, no M, no J — the domain is still MESHED (the hole is the same
    # shape) but the field cannot tell it from the air around it.  A part that
    # is `reference` is deliberately untouched here: its physics is identical
    # to `included`, only the accounting differs.
    _pstates = _excluded_parts()
    if _pstates:
        def _air(tag: int, label: str) -> None:
            if tag in mats:
                mats[tag] = FEMMaterial(name=f"{label}(excluded→air)", mu_r=1.0)
        if "stator_core" in _pstates:
            _air(DOM_STATOR, "stator")
        if "rotor_core" in _pstates:
            _air(DOM_ROTOR, "rotor")
        if "shaft" in _pstates:
            _air(DOM_SHAFT, "shaft")
        if "sleeve" in _pstates:
            _air(DOM_SLEEVE, "sleeve")
        if "magnet" in _pstates:
            for _t in [t for t in mats
                       if (DOM_MAG_BASE <= t < DOM_COIL_BASE)
                       or t in (DOM_MAG_N, DOM_MAG_S)]:
                _air(_t, "magnet")
        if "slot" in _pstates:
            for _t in [t for t in mats
                       if t >= DOM_COIL_BASE or t == DOM_COIL]:
                _air(_t, "coil")
    return mats


def _excluded_parts() -> frozenset:
    """The set of part keys the caller has EXCLUDED, or an empty set.

    Read from the same channel the material assignment travels on (shared
    config ``parts:`` + this request's material context), so nothing new has to
    be threaded through the solver's call graph — and every cache key that
    already hashes the material context separates the states for free.
    """
    try:
        from motor_ai_sim.part_states import resolve as _rs, EXCLUDED as _EX
        return frozenset(k for k, v in _rs().items() if v == _EX)
    except Exception:      # noqa: BLE001 — no state map is the default machine
        return frozenset()


def _shaft_skin_material() -> Tuple[float, float]:
    """(σ [S/m], μ_r,max) of the shaft as the eddy solve will see it — the
    same assignment precedence as the σ block in fem_transient_sliding_band
    (shared config, then the request's material context, override-carried
    props first).  σ = 0 for an EXCLUDED shaft (it is air).  μ_r,max is the
    largest secant μ of the assigned B-H curve (1 for a non-magnetic card):
    the thinnest skin the steel can have, which is what a mesh must resolve."""
    if "shaft" in _excluded_parts():
        return 0.0, 1.0
    from motor_ai_sim import materials as _ml
    from motor_ai_sim.config import get_material_assignments as _gma_s
    from motor_ai_sim.simulation.conductor_skin import bh_mu_r_max
    _ma_s = dict(_gma_s() or {})
    try:
        from motor_ai_sim.material_context import get_request_materials as _grm_s
        _ctx = _grm_s() or {}
    except Exception:      # noqa: BLE001
        _ctx = {}
    _ma_s.update({k: v for k, v in (_ctx.get("assignment") or {}).items() if v})
    _props = _ctx.get("materials") or {}
    name = str(_ma_s.get("shaft", "") or "")
    obj = None
    for _cat in ("conductor", "steel", "magnet", "insulator"):
        try:
            if name and name in _props:
                _c = (_props[name] or {}).get("category") or _cat
                obj = _ml.material_from_dict(_c, name, _props[name])
            else:
                obj = _ml.get_material(_cat, name)
            break
        except Exception:  # noqa: BLE001
            obj = None
    sig = float(getattr(obj, "sigma", 0.0) or 0.0) if obj is not None else 0.0
    if not sig:
        sig = SIGMA_SHAFT
    mu = bh_mu_r_max(getattr(obj, "bh_curve", None)) if obj is not None else 1.0
    mu = max(mu, float(getattr(obj, "mu_r", 1.0) or 1.0) if obj is not None else 1.0)
    return sig, mu


def _field2d_static_inputs(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
    magnet_temp_c: Optional[float] = None,
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
    # EFFECTIVE parallel count = connection paths x strands in hand.  Winding k
    # wires in hand leaves the slot's `num_wires_per_slot` conductors where they
    # are (the source below is still N.I ampere-turns over the real copper) but
    # only n_wires/k of them are in SERIES, so the slot MMF — and every quantity
    # built on it — divides by k exactly as another parallel path would.
    from motor_ai_sim.winding import (n_parallel_effective as _npar_eff,
                                      conductors_per_slot as _cond_slot)
    n_parallel   = _npar_eff(wind.get("n_parallel", 2), geo)
    # CONDUCTORS per slot = num_wires_per_slot × wire_split.  Each strip is its
    # own drawn conductor, so the slot's ampere-turns are conductors × the
    # current in ONE of them — and `n_parallel` above already carries the ÷N
    # when those strips are in parallel, which is what makes the parallel mode
    # come out identical in MMF.  wire_split = 1 leaves both untouched.
    n_wires      = _cond_slot(geo) or geo.get("num_wires_per_slot", 14)
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
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg, slot_area,
                           n_wires, magnet_temp_c=magnet_temp_c)
    return polys, mats, p


def fem_field2d(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    grid_size: int = 150,
    mesh_size_mm: float = 1.6,
    magnet_temp_c: Optional[float] = None,
) -> FEMResult:
    """Top-level: build mesh + assemble + solve + sample.

    Mirrors the signature of routes/simulation.py::get_field2d so the FEM
    endpoint can drop in as a swap.
    """
    import time as _t

    t_start = _t.time()
    polys, mats, p = _field2d_static_inputs(rotor_angle_deg, gamma_deg,
                                            magnet_temp_c=magnet_temp_c)

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
    magnet_temp_c: Optional[float] = None,
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
    _polys, mats, _p = _field2d_static_inputs(rotor_angle_deg, gamma_deg, I_phase_rms,
                                              magnet_temp_c=magnet_temp_c)
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
# Hoop-wound T800 UD CFRP retaining sleeve, TRANSVERSE to the fibres — which is
# the direction the 2-D solver's induced current (J_z, axial) actually flows.
# Along the fibres it is ~3e4 S/m; using that here would over-read the sleeve's
# eddy loss by ~400x for a current that cannot follow the fibres.  Last-resort
# default only: the real number comes from the assigned material's `sigma`.
SIGMA_SLEEVE = 80.0              # S/m






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
    # the wire COLUMN (wire_split's strips + their gaps), same as
    # geometry_2d.params_from_config; == wire_width at wire_split = 1
    from motor_ai_sim.winding import winding_footprint_mm as _fp_geo
    slot_width_m = (_fp_geo(g) + 2 * g["wire_spacing_x"]
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


def _step_voltage_series(psi, cur, R, dts, prev=None):
    """Winding voltage per time STEP, exactly as the time integration sees it.

    For the step (t_{k-1}, t_k] ending at frame k:

        v_{k-1/2} = R · (i_k + i_{k-1}) / 2  +  (ψ_k − ψ_{k-1}) / Δt_k

    This is the Crank–Nicolson circuit row the voltage drive SOLVES
    (simulation/drive.circuit_residual_ll): on an imposed-voltage run it is
    the applied voltage itself, to the Newton residual.  On an imposed-current
    run it is the voltage a CN voltage drive would have to apply to reproduce
    the solved currents.  And whatever the drive, (ψ_k − ψ_{k-1})/Δt_k is not
    an approximation of anything: by Faraday it is the EXACT mean EMF over the
    step (∫dψ = Δψ).  No truncation, no smoothing — every harmonic the solve
    resolved is in it, and it converges with the step count like the solve
    itself (O(Δt²) against the instantaneous voltage at the step midpoint).

    It replaces a spectral derivative truncated above the second slot
    harmonic (owner 2026-09-24: no filter may shape a reported value).

    ``prev`` = (ψ, i) of the frame solved immediately BEFORE the window (the
    settling prefix / eddy warm-up), or None for a window with no solved
    predecessor, which is then closed periodically (frame N−1 is one step
    before frame 0 of the next period — exact for an imposed-current run,
    whose field is periodic).  Returns ``(v, i_mid)``: the step voltages and the
    step-mean currents they multiply in the power ⟨v·i⟩ — the pairing under
    which a lossless inductor exchanges exactly zero energy over a period.
    """
    x = np.asarray(psi, float)
    i = np.asarray(cur, float)
    d = np.asarray(dts, float)
    N = x.size
    if N == 0:
        return np.zeros(0), np.zeros(0)
    if prev is None:
        x0, i0 = float(x[-1]), float(i[-1])
    else:
        x0, i0 = float(prev[0]), float(prev[1])
    xm = np.concatenate([[x0], x[:-1]])
    im = np.concatenate([[i0], i[:-1]])
    i_mid = 0.5 * (i + im)
    return R * i_mid + (x - xm) / d, i_mid


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


# ── Cross-run eddy warm cache (optimizer iterations) ────────────────────────
# One slot holding the near-final eddy field of the last eddy transient in
# this process.  Optimizer iterations move the geometry a LITTLE and keep the
# operating point, so the solid-eddy steady state of run N is almost run N−1's
# — seeding the eddy history with it lets the 2-frame settle probe pass at
# once instead of splicing a whole extra electrical period in front of the
# measured window (the dominant warm-up cost: probe 2 frames → +n_spp frames
# whenever the cold start has not decayed).  Accuracy is unchanged BY
# CONSTRUCTION: the adaptive settle test still judges the handoff by the same
# residual tolerance, so a seed from a too-different geometry or current
# simply fails it and the run extends exactly as a cold one would.
#
# Optimizer evals run ONE PER SUBPROCESS (refine_proc), so the in-memory slot
# alone would never survive an iteration — the cache is therefore mirrored to
# config/.warm_cache.npz (atomic tmp+replace; concurrent scan workers race
# benignly, last writer wins, every reader gets a complete file).
# Migration Stage 3: the in-memory half is per WORKSPACE, like the .npz mirror
# it shadows (per workspace since Stage 1).  A seed is a FIELD STATE: served
# across workspaces it is not a warm start, it is another machine's answer with
# a head start, and the settle test would have to catch every one of them.  One
# named slot ("last"); the cap is a safety valve.
from motor_ai_sim import workspace as _WSW
_SB_WARM_CACHE = _WSW.ws_map("fem_solver_2d.warm_cache", 4)

# A solve that must NOT read or publish the cross-run warm seed, decided per
# CALL rather than per process.  WHY (2026-09-07, "10 of 10 designs ran out of
# time"): the Thermal tab's loss solve - a FULL-RING eddy run at the operating
# point - published its state into config/.warm_cache.npz; the sweep that ran
# next (1/2 sector, same steps/temperature/speed) read it as a usable seed,
# priced every point at the seeded cost, and then every eval refused the
# foreign-mesh state, fell back to the cold march and was killed at the seeded
# hang cap.  The env var `SB_NO_WARM_CACHE=1` is the process-wide switch the
# regression tests use; this ContextVar is the per-request one a route sets
# around a solve that is not part of the seed lineage.
import contextvars as _ctxv
_NO_WARM_CACHE_CTX = _ctxv.ContextVar("sb_no_warm_cache", default=False)


def _warm_cache_disabled() -> bool:
    """True when this solve must neither consume nor publish the warm seed."""
    return _os_sb.environ.get("SB_NO_WARM_CACHE") == "1" or bool(_NO_WARM_CACHE_CTX.get())


def _warm_cache_path():
    # Per WORKSPACE (Stage 1).  Cross-user seeding would seed the WRONG MACHINE:
    # the seed is a field state, and a field state from someone else's geometry
    # is not a warm start, it is a wrong answer with a head start.
    from motor_ai_sim.workspace import root as _ws_root
    return _ws_root() / ".warm_cache.npz"


# ── SWEEP MODE: seed the next point from the previous one ───────────────────
# User, 2026-09-06: "we already agreed that the demagnetization pass is done
# once per sweep only; the geometry changes are small, and each next
# calculation is seeded from the previous one."  Sweep points had gone from
# 700-800 s to 1100-1700 s because every subprocess eval started COLD: a full
# eddy warm-up march from zero AND — since the 2026-09-05 reproducibility fix —
# a full extra electrical period of demag PRE-PASS on top.
#
# SB_SEED_FROM_PREVIOUS=1 is set by the optimizer/sweep coordinator in
# `_EVAL_ENV` (routes/optimization.py) and by NOTHING else.  The INTERACTIVE
# Simulation path never sets it, so every number the user sees in the app is
# produced by exactly the code that produced it before this flag existed —
# including the 2026-09-05 pre-pass and its reproducibility guarantee.
#
# What the flag changes, and only under it:
#   * the eddy seed is accepted at ANY current and ANY γ (the settle test at
#     the handoff still judges the handoff — see the quiet test; a seed from
#     too far away simply fails it and the march extends exactly as a cold one
#     would).  nspp / n_periods / winding / coil temperature / magnet scale
#     must still match and |Δrpm| must still be within 5 %.
#   * when the seed also carries the Br RATCHET state, the run starts from
#     that magnet with the ratchet active from the first REPORTED frame, and
#     the dedicated one-period pre-pass is skipped.  The ratchet is monotone
#     and the sweep queue runs lowest current first, so continuing from the
#     previous point's Br is the physically right state for the next point —
#     it is the same magnet, one small geometry step later.
def _seed_from_previous() -> bool:
    return _os_sb.environ.get("SB_SEED_FROM_PREVIOUS") == "1"


def _geo_fingerprint(geo: dict) -> str:
    """A short, stable hash of the MERGED geometry a run solved.

    Travels with the published seed so a result can say WHICH design's magnet
    state it inherited — "seeded" without naming the parent is not honesty.

    Every numeric knob counts, ``wire_split`` included: N strips of wire_width
    side by side is a different MACHINE (turns ×N, ψ ×N, R ×N²) as well as a
    wider slot, and a fingerprint that skipped it would hand a split run's
    cached magnet state to an unsplit one.  Bools are hashed as 0/1 — no
    geometry knob is one today (``wire_split_series`` was, for a few hours on
    2026-09-08), but a hand-edited yaml can still carry ``true``/``false`` and
    it must hash the same as 1/0.
    """
    try:
        import hashlib as _hl
        items = sorted((str(_k), float(_v)) for _k, _v in (geo or {}).items()
                       if isinstance(_v, (int, float, bool)))
        return _hl.sha1(";".join("%s=%.6g" % _it for _it in items)
                        .encode("utf-8")).hexdigest()[:16]
    except Exception:       # a diagnostic may never fail a run
        return ""


def _warm_cache_load(disk_only: bool = False) -> Optional[dict]:
    """The last published eddy state: memory first, then the disk mirror.

    ``disk_only`` is for a caller that is not the consumer — the sweep
    coordinator asking what an eval SUBPROCESS would find.  A subprocess starts
    with an empty `_SB_WARM_CACHE`, so the disk mirror is the whole of its
    truth, while the API process's memory can hold an older interactive run's
    state that no subprocess will ever see.
    """
    wc = _SB_WARM_CACHE.get("last")
    if wc is not None and not disk_only:
        return wc
    try:
        p = _warm_cache_path()
        if not p.is_file():
            return None
        with np.load(p, allow_pickle=False) as z:
            _f = set(z.files)
            wc = {"doflocs": z["doflocs"], "A": z["A"], "Ued": z["Ued"],
                  "solid": z["solid"],
                  "meta": {"nspp": int(z["nspp"]), "npd": float(z["npd"]),
                           "conn": str(z["conn"]), "temp": float(z["temp"]),
                           "mscale": float(z["mscale"])},
                  "I": float(z["I"]), "rpm": float(z["rpm"]),
                  "gam": float(z["gam"]),
                  "geo_fp": (str(z["geo_fp"]) if "geo_fp" in _f else ""),
                  "nspp_req": (int(z["nspp_req"]) if "nspp_req" in _f
                               else int(z["nspp"])),
                  # sector count the state was solved on (0 = unknown, written
                  # before 2026-09-07); a seed from another symmetry is refused
                  "nsect": (int(z["nsect"]) if "nsect" in _f else 0)}
            # The Br RATCHET state (2026-09-06).  OPTIONAL by construction: a
            # cache written before this existed, or by a run with demag off,
            # carries none and the reader must not care.
            if {"br_val", "br_cen", "br_tag"} <= _f:
                wc["br_val"] = np.asarray(z["br_val"], float)
                wc["br_cen"] = np.asarray(z["br_cen"], float)
                wc["br_tag"] = np.asarray(z["br_tag"], int)
            # per-group same-angle reference (2026-09-24); optional
            wc["solid_grp"] = {_fk[len("solid_grp_"):]: np.asarray(z[_fk], float)
                               for _fk in _f if _fk.startswith("solid_grp_")}
            # eddy time scheme (2026-09-25): only a BDF2 state carries the key
            # and its second history level; an older mirror is backward Euler
            if "ts" in _f:
                wc["meta"]["ts"] = str(z["ts"])
            if "A2" in _f:
                wc["A2"] = z["A2"]
        return wc
    except Exception:
        return None


def _warm_cache_store(wc: dict) -> None:
    _SB_WARM_CACHE["last"] = wc
    try:
        p = _warm_cache_path()
        tmp = p.with_suffix(".npz.tmp%d" % _os_sb.getpid())
        m = wc["meta"]
        _extra = {_k: wc[_k] for _k in ("br_val", "br_cen", "br_tag")
                  if wc.get(_k) is not None}
        for _gk, _gv in (wc.get("solid_grp") or {}).items():
            _extra["solid_grp_" + str(_gk)] = np.asarray(_gv, float)
        if m.get("ts"):
            _extra["ts"] = str(m["ts"])
        if wc.get("A2") is not None:
            _extra["A2"] = wc["A2"]
        np.savez(tmp, doflocs=wc["doflocs"], A=wc["A"], Ued=wc["Ued"],
                 solid=wc["solid"],
                 nspp=m["nspp"], npd=m["npd"], conn=m["conn"], temp=m["temp"],
                 mscale=m["mscale"], I=wc["I"], rpm=wc["rpm"], gam=wc["gam"],
                 geo_fp=str(wc.get("geo_fp") or ""),
                 nspp_req=int(wc.get("nspp_req") or m["nspp"]),
                 nsect=int(wc.get("nsect") or 0), **_extra)
        # np.savez may append ".npz" to the tmp name — resolve what it wrote.
        src = tmp if tmp.is_file() else tmp.with_suffix(tmp.suffix + ".npz")
        # RETRY the replace.  On Windows an open READ handle blocks the rename
        # (measured: os.replace over a file another process holds open raises
        # WinError 5), and a reader is exactly what this cache is for — a
        # concurrent eval seeding itself, or the sweep coordinator asking
        # whether a seed exists.  Without the retry that collision silently
        # DROPPED the publish, and the next point started cold for no reason
        # anyone could see.  Readers hold the file for milliseconds, so a few
        # short backoffs cover it; a permanent failure still never fails a run.
        import time as _t_wc
        for _try_wc in range(6):
            try:
                _os_sb.replace(src, p)
                return
            except OSError:
                _t_wc.sleep(0.02 * (_try_wc + 1))
        log.debug("warm cache: could not replace %s after 6 tries (a reader "
                  "held it open); this point's state is not published", p)
        try:
            src.unlink()          # no orphan tmp files beside the config
        except Exception:
            pass
    except Exception:   # the disk mirror is best-effort, never fails a run
        pass


def _warm_cache_meta() -> Optional[dict]:
    """The disk mirror's IDENTITY only — no field arrays.

    The sweep coordinator asks "would a queued eval find something it can
    continue?" once per eval; loading the whole A vector to answer that would
    read megabytes per question.  `np.load` on an npz is lazy, so touching only
    the scalars costs the zip directory and a few bytes.  Returns
    ``{meta, I, rpm, gam, geo_fp, has_br}`` or None.
    """
    try:
        p = _warm_cache_path()
        if not p.is_file():
            return None
        with np.load(p, allow_pickle=False) as z:
            _f = set(z.files)
            _mt = {"nspp": int(z["nspp"]), "npd": float(z["npd"]),
                   "conn": str(z["conn"]), "temp": float(z["temp"]),
                   "mscale": float(z["mscale"])}
            if "ts" in _f:
                _mt["ts"] = str(z["ts"])
            return {"meta": _mt,
                    "I": float(z["I"]), "rpm": float(z["rpm"]),
                    "gam": float(z["gam"]),
                    "geo_fp": (str(z["geo_fp"]) if "geo_fp" in _f else ""),
                    # The REQUESTED steps/period — what a coordinator can
                    # compare against.  Falls back to the snapped count for a
                    # mirror written before 2026-09-06.
                    "nspp_req": (int(z["nspp_req"]) if "nspp_req" in _f
                                 else int(z["nspp"])),
                    "nsect": (int(z["nsect"]) if "nsect" in _f else 0),
                    "has_br": bool({"br_val", "br_cen", "br_tag"} <= _f)}
    except Exception:
        return None


# ── Optimizer fix E (2026-09-24): SB_SEED_ACROSS_STEPS ──────────────────────
# ONE separate hook, set only by routes/optimization for a final-quality
# ("cogging_quality") optimizer eval (winner validation, Apply check) when
# OPT_FINAL_WARM_START is on — never by the Simulation tab.  It lets such a run
# continue the EDDY HISTORY of a warm state solved at ANOTHER steps/period (the
# optimization candidates' coarser schedule); every other meta term must still
# match.  The same-angle reference is withheld automatically (its per-frame
# samples have the parent's length), so the residual-trend settle test alone
# decides the handoff, exactly as for a seed from another operating point.
# The Br RATCHET map of such a state is NOT continued (_br_withheld_across_
# steps): it is the optimization lineage's accumulated de-rating, and the A/B
# (docs/OPTIMIZER_ITEMS_DE_2026-09-24.md) measured it moving the standard
# torque by ~1 %; the standard run pays its own demag pre-pass on a fresh
# magnet instead, exactly as a cold standard run does.
def _br_withheld_across_steps(wc, wmeta) -> bool:
    try:
        return (_os_sb.environ.get("SB_SEED_ACROSS_STEPS") == "1"
                and int((wc.get("meta") or {}).get("nspp", -1)) != int(wmeta.get("nspp")))
    except Exception:           # unreadable meta: never continue that Br
        return True


def _seed_across_steps_ok(cached_meta, wmeta) -> bool:
    if _os_sb.environ.get("SB_SEED_ACROSS_STEPS") != "1":
        return False
    if not isinstance(cached_meta, dict) or not isinstance(wmeta, dict):
        return False
    _a = {k: v for k, v in cached_meta.items() if k != "nspp"}
    _b = {k: v for k, v in wmeta.items() if k != "nspp"}
    if _a != _b:
        return False
    log.info("P2 eddy warm cache: seed solved at %s steps/period accepted for "
             "this %s-step standard run (SB_SEED_ACROSS_STEPS, optimizer fix E)",
             cached_meta.get("nspp"), wmeta.get("nspp"))
    return True


def _warm_seed_accept(wc: Optional[dict], wmeta: dict, I_phase_rms: float,
                      rpm: float, gamma_deg: float, n_sectors: Optional[int] = None):
    """May this cached state seed THIS run?  ``(ok, why_not)``.

    ONE gate for both halves of the seed (the eddy history and the Br map), so
    the two can never disagree about which parent state a run is continuing.

    The meta terms are HARD refusals in every mode: a different steps/period,
    n_periods, winding, coil temperature or magnet scale is a different
    discretisation or a different machine, and a projected field would not be
    the same physical state.  Speed is hard too — f_elec sets the whole ∂A/∂t
    scale, so a seed from another speed is a field the machine was never in.

    Current and γ are the ones SB_SEED_FROM_PREVIOUS relaxes (user 2026-09-06),
    and the relaxation is safe for exactly one reason: the settle ("quiet") test
    at the handoff is what actually decides whether the seeded history is usable
    — an operating point too far away leaves a residual above _EDDY_SETTLE_TOL
    and the march extends by a whole electrical period, i.e. it degrades to the
    cold cost, never to a wrong answer.
    """
    if wc is None:
        return False, "no cached state"
    if wc.get("meta") != wmeta and not _seed_across_steps_ok(wc.get("meta"), wmeta):
        return False, ("different schedule or machine (steps/period, periods, "
                       "winding, coil temperature or magnet scale)")
    # Symmetry is part of the discretisation: the eddy history `Ued` is one
    # value per solid conductor of the SOLVED wedge, so a full-ring state has a
    # different conductor set than a 1/2-sector run and cannot continue it
    # (2026-09-07: such a seed sent a whole sweep to the cold march under the
    # seeded hang cap).  0 = a state written before this field existed.
    if n_sectors is not None and int(wc.get("nsect") or 0) > 0:
        _ns_run = 1 if int(n_sectors) <= 1 else int(n_sectors)
        if int(wc["nsect"]) != _ns_run:
            return False, "cached state was solved on %d sector(s), this run %d" % (
                int(wc["nsect"]), _ns_run)
    if abs(float(wc["rpm"]) - float(rpm)) > 0.05 * max(abs(float(rpm)), 1e-9):
        return False, "speed differs by more than 5 %"
    if _seed_from_previous():
        return True, ""
    if abs(float(wc["I"]) - float(I_phase_rms)) > 0.05 * max(
            abs(float(I_phase_rms)), 1e-9):
        return False, "current differs by more than 5 %"
    if abs(float(wc["gam"]) - float(gamma_deg)) > 5.0:
        return False, "gamma differs by more than 5 deg"
    return True, ""


def _project_br(wc: dict, cen_new: np.ndarray, tag_new: np.ndarray):
    """Cached Br map → THIS mesh, by nearest element centroid WITHIN a magnet.

    The optimizer moves the geometry a little between points, so the mesh is a
    different one and the element ordering means nothing.  Matching is done per
    magnet DOMAIN TAG (the tags are the machine's own magnet ids and are stable
    across a geometry step) and only then by nearest centroid, so a magnet can
    never inherit its neighbour's de-rating just because the two are close at a
    corner.  A tag the seed has never seen keeps its pristine 1.0.

    Returns ``(kept_factor_per_element, n_matched)`` or ``None``.
    """
    try:
        from scipy.spatial import cKDTree as _KDT
    except Exception:
        return None
    _val = np.asarray(wc.get("br_val"), float)
    _cen = np.asarray(wc.get("br_cen"), float)
    _tag = np.asarray(wc.get("br_tag"), int)
    if _val.size == 0 or _cen.shape[0] != _val.size or _tag.size != _val.size:
        return None
    out = np.ones(int(np.size(tag_new)), float)
    n_hit = 0
    for _t in np.unique(tag_new):
        _src = np.where(_tag == int(_t))[0]
        _dst = np.where(tag_new == int(_t))[0]
        if _src.size == 0 or _dst.size == 0:
            continue
        _j = _KDT(_cen[_src]).query(cen_new[_dst], k=1)[1]
        out[_dst] = _val[_src][_j]
        n_hit += int(_dst.size)
    if n_hit == 0:
        return None
    return out, n_hit


# Conductor-area spread inside one slot that is still "the same wire drawn n
# times" (the meshed rectangles of one slot agree to ~0.2 % today), and the
# spread beyond which a body cannot be a strip of that wire at all.
_EDDY_CON_AREA_WARN_RTOL = 0.05
_EDDY_CON_AREA_FAIL_RTOL = 0.50


def check_eddy_conductor_bodies(coil_con, coil_area_m2, n_wires, *,
                                n_slots_expected=None,
                                warn_rtol=_EDDY_CON_AREA_WARN_RTOL,
                                fail_rtol=_EDDY_CON_AREA_FAIL_RTOL,
                                log=None):
    """Refuse an eddy solve whose meshed conductor bodies are not the winding.

    The coupled eddy solve imposes ``∫J dΩ = ±I_branch`` on EVERY conductor
    body (``Iunit = ±1`` since 2026-09-23 — one physical conductor, one branch
    current; ``docs/eddy-series-current-normalization-2026-09-23.md``).  The
    slot's ampere-turns are therefore ``(bodies in the slot) × I_branch`` and
    they equal the magnetostatic source's ``n_wires × I_branch`` ONLY when the
    mesh carries exactly ``n_wires`` bodies per slot — ``num_wires_per_slot ×
    wire_split`` (``winding.conductors_per_slot``): every drawn strip is a
    body, strands in hand are rows of their own, and the bonding mode
    (transposed / parallel / series paths) only groups these bodies AFTER they
    are built, never changes their number.  The old area-weighted ``Iunit``
    stayed right when a drawing dropped, merged or clipped a body; this one is
    wrong by one branch current per missing body, silently, so the count is
    checked HERE, on the bodies as built, before any current is imposed.

    Raises ``RuntimeError`` naming the slot, the expected and the found count
    when a slot does not hold ``n_wires`` bodies, when a body's slot cannot be
    read off its material name, or when a sector model does not hold
    ``n_slots_expected`` whole slots.

    Areas.  The constraint does NOT assume equal areas — ``∫J = I`` holds for a
    body of any section, and the area enters only that body's DC reference
    (``I²/S``) and the eddy redistribution inside it — so an unequal area is
    not a wrong ampere-turn.  What it IS is a body that is not the wire the
    parameters describe: every conductor of a slot is the same ``wire_width ×
    wire_height`` strip, so its meshed area must match its neighbours'.  A
    spread above ``warn_rtol`` (5 %) is a clipped / shrunk stack — the CAD does
    that to fit a slot, ``geometry_validation`` already flags it as a WARNING,
    and the machine is solvable with less copper than described — so it is
    logged and recorded, not refused.  A body under half its slot's median
    (``fail_rtol``) is not a strip of that wire at all (a sliver, a body cut by
    the sector boundary) and imposing a full branch current on it is an
    impossible machine: refused.

    ``coil_con`` are the per-body dicts with ``tag``, ``slot``, ``phase``;
    ``coil_area_m2`` maps tag → meshed area.  Returns a small summary dict for
    the result record.
    """
    n_wires = int(n_wires)
    per_slot: dict = {}
    for c in coil_con:
        per_slot.setdefault(int(c.get("slot", -1)), []).append(int(c["tag"]))
    problems = []
    if -1 in per_slot:
        problems.append(
            "%d conductor body(ies) whose slot cannot be read off the material "
            "name (tags %s)" % (len(per_slot[-1]), sorted(per_slot[-1])[:12]))
    slots = sorted(s for s in per_slot if s >= 0)
    for s in slots:
        if len(per_slot[s]) != n_wires:
            problems.append("slot %d: expected %d conductor bodies "
                            "(num_wires_per_slot x wire_split), found %d"
                            % (s, n_wires, len(per_slot[s])))
    if n_slots_expected is not None and len(slots) != int(n_slots_expected):
        problems.append("the meshed sector holds %d slot(s) (%s), expected %d"
                        % (len(slots), slots, int(n_slots_expected)))
    if problems:
        raise RuntimeError(
            "eddy conductor bodies are not the winding: " + "; ".join(problems)
            + ".  Each meshed conductor is imposed one branch current, so a "
            "slot with the wrong body count is solved at the wrong "
            "ampere-turns.  Refusing rather than solving a machine that is not "
            "the one described (check the coil polygons / sector cut).")
    warnings_out = []
    worst = 0.0
    for s in slots:
        a = np.asarray([float(coil_area_m2.get(t, 0.0) or 0.0)
                        for t in per_slot[s]], float)
        med = float(np.median(a)) if a.size else 0.0
        if med <= 0.0:
            raise RuntimeError(
                "eddy conductor bodies are not the winding: slot %d has no "
                "meshed copper area (tags %s)" % (s, per_slot[s]))
        dev = np.abs(a / med - 1.0)
        i_w = int(np.argmax(dev))
        worst = max(worst, float(dev[i_w]))
        if dev[i_w] > fail_rtol:
            raise RuntimeError(
                "eddy conductor bodies are not the winding: slot %d, tag %d "
                "has %.4g mm2 of copper against the slot's median conductor "
                "%.4g mm2 (%.0f %% off) — a body that small is not a strip of "
                "this wire (a sliver or a body cut by the sector boundary), "
                "and imposing a full branch current on it is not the machine."
                % (s, per_slot[s][i_w], 1e6 * a[i_w], 1e6 * med,
                   100.0 * dev[i_w]))
        if dev[i_w] > warn_rtol:
            msg = ("slot %d: conductor areas spread %.1f %% (tag %d %.4g mm2 "
                   "vs median %.4g mm2) — a clipped / shrunk stack; the "
                   "ampere-turns are still exact, that body just has less "
                   "copper than wire_width x wire_height"
                   % (s, 100.0 * dev[i_w], per_slot[s][i_w], 1e6 * a[i_w],
                      1e6 * med))
            warnings_out.append(msg)
            if log is not None:
                log.warning("eddy conductor bodies: %s", msg)
    summary = {"n_wires_per_slot": n_wires, "n_slots_meshed": len(slots),
               "n_bodies": sum(len(per_slot[s]) for s in slots),
               "area_spread_max_rel": round(float(worst), 6),
               "area_warnings": warnings_out}
    if log is not None:
        log.info("eddy conductor bodies: %d slot(s) x %d = %d bodies, conductor "
                 "area spread <= %.3f %% within a slot%s",
                 len(slots), n_wires, summary["n_bodies"], 100.0 * worst,
                 "" if not warnings_out else " (%d WARNING(s))" % len(warnings_out))
    return summary


def _p2_virtual_work_torque(p2, field, source, motion, shift,
                            sector_count, stack_length_m):
    """Diagnostic torque from the full pointwise field residual (mechanical rad)."""
    stiffness, _ = p2.Kpw(field)
    residual = np.asarray(stiffness @ field - source).ravel()
    derivative = motion.motion_derivative(field, shift)
    return -float(sector_count) * float(stack_length_m) * float(
        residual @ derivative)


def _p2_virtual_work_ineligible_reason(*, eddy, voltage_drive, demag,
                                       frozen_nu, saturable, newton_ok):
    """Only the accepted, imposed-current pointwise Newton path is comparable."""
    if eddy:
        return "coupled_eddy_field"
    if voltage_drive:
        return "voltage_drive_field"
    if demag:
        return "irreversible_demagnetisation"
    if frozen_nu:
        return "frozen_permeability"
    if not saturable:
        return "linear_path_not_certified"
    if not newton_ok:
        return "pointwise_newton_not_accepted"
    return None


SamplingPurpose = Literal["standard", "optimization", "cogging_quality",
                          "internal_probe"]
_SAMPLING_PURPOSES = ("standard", "optimization", "cogging_quality",
                      "internal_probe")
# Raw samples per cogging cycle each purpose is JUDGED against (record +
# warning). Only "cogging_quality" RAISES the frame count to meet it.
_COGGING_TARGET_SAMPLES = {"standard": 6, "optimization": 3,
                           "cogging_quality": 6, "internal_probe": 6}


def _sampling_purpose(value: SamplingPurpose) -> SamplingPurpose:
    if type(value) is not str or value not in _SAMPLING_PURPOSES:
        raise ValueError("sampling_purpose must be one of "
                         + ", ".join(repr(v) for v in _SAMPLING_PURPOSES))
    return value


def _cogging_frame_policy(num_slots, num_poles, pole_pairs, actual_steps,
                          nodes_per_period, *, continuous_angle=False,
                          internal_daxis_calibration=False,
                          sampling_purpose: SamplingPurpose = "standard"):
    """Judge (and, in cogging-quality mode only, raise) the raw angle density.

    Held-item fix of 8997bb3 / 821f3df (2026-09-24, owner-approved review):
    six raw samples per slot/pole cogging cycle is an OPT-IN quality mode, not
    the default for every run.

    * ``"cogging_quality"`` — the dedicated cogging run: the frame count is
      raised to ≥ 6 raw samples per cogging cycle (existing slip-ring divisor,
      never a mesh change), exactly as 8997bb3 did for every run.
    * ``"standard"`` / ``"optimization"`` — the requested (snapped) steps are
      kept, as in 68de0ca; a run below its target (6 / 3 samples per cycle) is
      recorded as insufficient FOR COGGING and warned about, nothing else.
    * ``"internal_probe"`` and the d-axis calibration — ψ_PM no-load probe,
      Ld/Lq probe and bench, d-axis calibration: always exempt, never raised,
      never warned about (they sample ψ, not cogging torque; 8997bb3 inflated
      them 6 → 96/120 frames).

    ``actual_steps`` is the existing whole-node snap result. A nodal band may
    only move to another divisor of its *existing* ring; this policy never
    changes the mesh. An analytic continuous-angle band has no such divisor.
    """
    sampling_purpose = _sampling_purpose(sampling_purpose)
    target = _COGGING_TARGET_SAMPLES[sampling_purpose]
    values = (num_slots, num_poles, pole_pairs, actual_steps, nodes_per_period)
    if any(isinstance(value, (bool, np.bool_)) or
           not isinstance(value, (int, np.integer)) or value <= 0
           for value in values):
        raise ValueError("cogging frame counts must be positive integers")
    slots, poles, pairs, steps, nodes = map(int, values)
    if poles != 2 * pairs:
        raise ValueError("num_poles must equal twice pole_pairs")
    order = math.lcm(slots, poles)
    if order % pairs:
        raise ValueError("cogging order must tile an electrical period")
    cycles = order // pairs
    minimum = target * cycles
    chosen = steps
    reason = None
    if internal_daxis_calibration:
        # This private solve samples psi_A to locate the d-axis. Resampling it
        # changes its peak-ambiguity criterion and can reject a valid motor;
        # it is not a reported torque/cogging run.
        reason = "internal_daxis_calibration_exempt"
    elif sampling_purpose == "internal_probe":
        reason = "internal_probe_exempt"
    elif steps < minimum and sampling_purpose != "cogging_quality":
        # 68de0ca behaviour: the requested resolution is the run's resolution.
        reason = "requested_steps_kept_below_cogging_target"
    elif steps < minimum:
        if continuous_angle:
            chosen = minimum
            reason = "raised_continuous_angle_to_target_samples_per_cycle"
        else:
            divisor = next((d for d in range(minimum, nodes + 1)
                            if nodes % d == 0), None)
            if divisor is None:
                reason = "insufficient_existing_slip_ring_divisor"
            else:
                chosen = divisor
                reason = "raised_to_existing_slip_ring_divisor"
    return {
        "purpose": sampling_purpose,
        "target_raw_samples_per_cycle": target,
        "cycles_per_electrical_period": cycles,
        "min_required_steps_per_period": minimum,
        "final_quality_min_required_steps_per_period": 6 * cycles,
        "steps_per_period": chosen,
        "raw_samples_per_cycle": chosen / cycles,
        "sufficient": chosen >= minimum,
        "final_quality_sufficient": chosen >= 6 * cycles,
        "auto_raised": chosen > steps,
        "reason": reason,
    }


#: What the worst demag element IS, shipped with it so no reader has to know.
BR_CORNER_NOTE = (
    "Worst single magnet element: a sharp-corner value that does not converge "
    "with mesh refinement (docs/NO_FILTERS_2026-09-24.md §3). A corner "
    "diagnostic, not the magnet's figure; the magnet's figure is the "
    "volume-weighted Br kept.")


def _demag_corner_diag(rotor_mesh, mag_idx, brm, ar_mag, mags):
    """The worst demagnetised element as a flagged CORNER diagnostic.

    ``100·min(Br factor)`` sits on the material curve's floor at every magnet
    mesh on Ø40/L13 and swings 98 → 79 % with the mesh on L155 (NO_FILTERS
    §3): it is the field singularity at a sharp magnet corner, the demag twin
    of the mechanical module's unaveraged peak stress.  So it ships with WHERE
    it is and how little of the magnet it covers, flagged, and never under a
    name a reader would take for the magnet's number.  The coordinates are the
    rotor mesh's own (rotor at 0°, the modelled sector), in mm.
    """
    brm = np.asarray(brm, float)
    iw = int(np.argmin(brm))
    el = int(np.asarray(mag_idx, int)[iw])
    xy = np.asarray(rotor_mesh.p, float)[:, np.asarray(rotor_mesh.t)[:, el]].mean(axis=1)
    x_mm, y_mm = 1e3 * float(xy[0]), 1e3 * float(xy[1])
    magnet = None
    for j, d in enumerate(mags or []):
        if el in set(np.asarray(d.get("idx", ()), int).tolist()):
            magnet = int(d.get("tag", j))
            break
    return {
        "flag": "corner",
        "br_pct": round(100.0 * float(brm[iw]), 1),
        "element": el,
        "magnet_tag": magnet,
        "x_mm": round(x_mm, 3), "y_mm": round(y_mm, 3),
        "r_mm": round(math.hypot(x_mm, y_mm), 3),
        "theta_deg": round(math.degrees(math.atan2(y_mm, x_mm)), 2),
        "element_area_pct": round(
            100.0 * float(ar_mag[iw]) / max(float(np.sum(ar_mag)), 1e-30), 4),
        "note": BR_CORNER_NOTE,
    }


# ── CONVERGED SETTLE for the plain sinusoidal voltage drive (2026-09-26) ────
# f83e60f cut the sinusoid's settle from 10 to a FIXED 4 periods (A/B on the
# fixtures); that count left ~1 % on the torque ripple (7.126 → 7.202 % at 40
# periods, docs/NO_FILTERS_2026-09-24.md open item 3).  A count is a guess about
# THIS machine's circuit; the period-to-period change of what is reported is a
# measurement of it.  So the settle now marches whole periods until the three
# reported quantities — phase-current amplitude, mean torque, torque ripple —
# each move by less than SB_V_SETTLE_TOL (relative) between consecutive
# settling periods, capped at SB_V_SETTLE_CAP periods.  The run records how
# many it used and the final residual, and says so when it hit the cap.
# An explicit SB_V_SETTLE_PERIODS still wins (a fixed count, as before).
_V_SETTLE_TOL_DEFAULT = 1e-3
_V_SETTLE_CAP_DEFAULT = 40


def _sine_settle_criterion():
    """(tol, cap) of the converged sine settle — validated loudly."""
    raw_t = (_os_sb.environ.get("SB_V_SETTLE_TOL") or "").strip()
    raw_c = (_os_sb.environ.get("SB_V_SETTLE_CAP") or "").strip()
    try:
        tol = float(raw_t) if raw_t else _V_SETTLE_TOL_DEFAULT
    except ValueError:
        raise ValueError("SB_V_SETTLE_TOL=%r is not a number" % raw_t) from None
    if not (math.isfinite(tol) and 0.0 < tol < 1.0):
        raise ValueError("SB_V_SETTLE_TOL must be a relative tolerance in "
                         "(0, 1), got %r" % tol)
    try:
        cap = int(raw_c) if raw_c else _V_SETTLE_CAP_DEFAULT
    except ValueError:
        raise ValueError("SB_V_SETTLE_CAP=%r is not an integer" % raw_c) from None
    if cap < 2:
        raise ValueError("SB_V_SETTLE_CAP must be >= 2 (two periods are the "
                         "least a period-to-period change needs), got %d" % cap)
    return tol, cap


def _settle_period_metrics(T, IA, IB, IC, dt_w):
    """The three reported quantities over ONE electrical period.

    Current amplitude = √2 × the phase rms (mean of the three phases' squares,
    weighted by each step's solved Δt — the rms the copper loss uses); torque
    mean and ripple of the per-frame torque series through ``torque_metrics``,
    the function the reported ripple comes from (plain mean, raw
    peak-to-peak / |mean|).  The reported T_avg is the energy-method mean,
    which differs from this series' mean by a fixed offset of the method, not
    by settling — so the period-to-period CHANGE of either measures the same
    drift.
    """
    T = np.asarray(T, float)
    w = np.asarray(dt_w, float)
    i2 = (np.asarray(IA, float) ** 2 + np.asarray(IB, float) ** 2
          + np.asarray(IC, float) ** 2) / 3.0
    i2m = float((i2 * w).sum() / max(float(w.sum()), 1e-300))
    _, rip = torque_metrics(T)
    return {"I_amp_A": math.sqrt(2.0 * i2m), "T_mean_Nm": float(T.mean()),
            "T_ripple_pct": (None if rip is None else float(rip)),
            "T_pp_Nm": float(np.ptp(T))}


def _settle_period_residual(prev, cur):
    """Relative period-to-period change of each reported quantity.

    Torque mean is scaled by max(|mean|, peak-to-peak) so a no-load run (mean
    ≈ 0) is judged on its waveform instead of dividing by nothing; the ripple
    is compared as the reported per cent when both periods have one, else as
    the peak-to-peak.  ``max`` is what the criterion reads.
    """
    def _rel(a, b, scale):
        return abs(float(b) - float(a)) / max(abs(float(scale)), 1e-300)
    r_i = _rel(prev["I_amp_A"], cur["I_amp_A"], cur["I_amp_A"])
    r_t = _rel(prev["T_mean_Nm"], cur["T_mean_Nm"],
               max(abs(cur["T_mean_Nm"]), cur["T_pp_Nm"]))
    if prev.get("T_ripple_pct") is not None and cur.get("T_ripple_pct") is not None:
        r_r = _rel(prev["T_ripple_pct"], cur["T_ripple_pct"], cur["T_ripple_pct"])
    else:
        r_r = _rel(prev["T_pp_Nm"], cur["T_pp_Nm"], cur["T_pp_Nm"])
    return {"I_amp": r_i, "T_mean": r_t, "T_ripple": r_r,
            "max": max(r_i, r_t, r_r)}


def _select_voltage_settle_periods(source_periods, *, sinusoidal_voltage,
                                   eddy, demag, explicit_override,
                                   converged_cap=_V_SETTLE_CAP_DEFAULT):
    """Settle count to SCHEDULE, and why.

    The plain sinusoidal voltage drive (no eddy, no demag, no explicit count)
    schedules ``converged_cap`` periods and stops early on the convergence
    criterion above; every other source keeps its own count unchanged.
    """
    periods = int(source_periods)
    if periods < 0 or periods != source_periods:
        raise ValueError("voltage settle periods must be a nonnegative integer")
    if (sinusoidal_voltage and not eddy and not demag and
            not explicit_override and periods == 10):
        return int(converged_cap), "plain_sinusoidal_voltage_converged_settle"
    return periods, "source_policy_unchanged"


class TdmAttemptFailed(RuntimeError):
    """A time-periodic (TDM) attempt that must not be reported: it failed at
    ``stage`` (set-up, static start, Newton, demag, re-solve, splice) or its
    reported period failed the acceptance gate.  Raised out of ONE solve
    (``_fem_transient_sliding_band_once``) so that nothing the attempt touched
    survives: :func:`fem_transient_sliding_band` re-runs from scratch —
    first with the strict state-residual stop when the gate failed on the
    owner's-terms stop, else (and then) as a march."""

    def __init__(self, stage: str, reason: str, info: Optional[Dict] = None,
                 retry_residual: bool = False, retry_full: bool = False) -> None:
        super().__init__("%s: %s" % (stage, reason))
        self.stage = str(stage)
        self.reason = str(reason)
        self.info = info
        # the one retry a gate failure earns: the strict state-residual stop
        # (after an owner's-terms stop) and/or the full period (after a half one)
        self.retry_residual = bool(retry_residual)
        self.retry_full = bool(retry_full)

    @property
    def retry(self) -> bool:
        return bool(self.retry_residual or self.retry_full)

    def record(self) -> Dict[str, Any]:
        return {"stage": self.stage, "reason": self.reason,
                "retry_residual": self.retry_residual,
                "retry_full_period": self.retry_full,
                "tdm": self.info}


def _drop_attempt(exc: BaseException) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Everything the wrapper keeps of a rejected attempt — its record, its
    message and its retry hint, as plain data — and NOTHING else (second Codex
    review, finding 9).  The exception's traceback holds every frame of the
    failed solve, and with them its meshes, factors, orbit and Jacobians (peak
    RSS up to 4.5 GB on the L155); its ``__cause__`` / ``__context__`` hold the
    inner failure's frames as well.  Those frames' locals are cleared and every
    link to them cut BEFORE the next attempt starts, so the retry and the march
    never run beside the failed attempt's arrays."""
    import traceback as _tb_mod
    rec = exc.record() if hasattr(exc, "record") else {"stage": "?", "reason": str(exc)}
    msg = str(exc)
    hint = {"retry": bool(getattr(exc, "retry", False)),
            "retry_residual": bool(getattr(exc, "retry_residual", False)),
            "retry_full": bool(getattr(exc, "retry_full", False))}
    seen = set()
    e: Optional[BaseException] = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        tb = e.__traceback__
        if tb is not None:
            try:
                _tb_mod.clear_frames(tb)
            except Exception:            # noqa: BLE001 — a running frame
                pass
        e.__traceback__ = None
        nxt = e.__cause__ or e.__context__
        e.__cause__ = None
        e.__context__ = None
        e = nxt
    if hasattr(exc, "info"):
        exc.info = None
    return rec, msg, hint


def fem_transient_sliding_band(*args, **kwargs) -> dict:
    """The sliding-band transient (``_fem_transient_sliding_band_once``) with
    TRANSACTIONAL time-periodic (TDM) attempts (Codex review, 2026-09-30).

    A TDM attempt runs inside one solve; if it fails at any stage, or its
    reported period fails the acceptance gate, that whole solve is DISCARDED
    (nothing it created is kept — the Br map, the magnet source, the demag
    state, the eddy histories, the factors and the warm cache all belong to
    it; the warm cache is written only after a solve that returns) and the
    request is solved again from scratch:

      1. TDM with the default stop (owner's terms or the 1e-7 state residual);
      2. if the closure march or the reported period failed its gate (second
         Codex review, 2026-10-03): TDM ONCE more with the Newton tightened to
         the strict 1e-7 state-residual stop, on the FULL period;
      3. otherwise, or if that fails too: a MARCH, with
         ``eddy_method_note = "march: TDM failed (...) — marched instead"``
         and every attempt recorded in ``tdm.attempts``.

    A rejected attempt leaves nothing behind: its traceback frames are cleared
    and every link to them cut before the next attempt starts
    (:func:`_drop_attempt`).  A march that is asked for (or that TDM cannot
    serve) runs once, as before.
    """
    import inspect as _insp
    # bound against the REAL solver signature (a test spy in its place takes
    # the same keywords)
    _sig = _insp.signature(_SB_ONCE_SIGNATURE_OF)
    kw = dict(_sig.bind_partial(*args, **kwargs).arguments)
    kw.pop("_tdm_ctl", None)
    attempts: List[Dict[str, Any]] = []
    _msg = ""
    _hint: Dict[str, Any] = {"retry": False}
    try:
        return _fem_transient_sliding_band_once(**kw)
    except TdmAttemptFailed as _e1:
        _r1, _msg, _hint = _drop_attempt(_e1)
        attempts.append(_r1)
        log.warning("SB TDM attempt 1 rejected (%s) — %s", _msg,
                    "retrying once with the strict state-residual stop on the "
                    "full period" if _hint["retry"] else "marching instead")
        del _e1
    if _hint["retry"]:
        try:
            return _fem_transient_sliding_band_once(
                **kw, _tdm_ctl={"stop": "residual", "period": "full",
                                "attempts": list(attempts)})
        except TdmAttemptFailed as _e2:
            _r2, _msg, _ = _drop_attempt(_e2)
            attempts.append(_r2)
            log.warning("SB TDM attempt 2 rejected (%s) — marching instead", _msg)
            del _e2
    note = ("march: TDM failed (%s) — marched instead" % (_msg,))
    return _fem_transient_sliding_band_once(
        **dict(kw, eddy_method="march"),
        _tdm_ctl={"fallback": {"note": note, "attempts": attempts}})


@_pardiso_scope          # each attempt owns (and releases) its PARDISO handles
def _fem_transient_sliding_band_once(
    n_steps_per_period: int = 12,
    sampling_purpose: SamplingPurpose = "standard",
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
    daxis_deg: Optional[float] = None,  # ← THE D-AXIS REFERENCE, GIVEN.  None = measure
                                 # it (a 24-frame no-load solve, ~39 s, cached per
                                 # geometry).  A number is taken AS IS and nothing
                                 # is solved for it: on a topology whose zero is
                                 # already known — 24s/28p sits on 60.000° across
                                 # every cross-section measured — re-deriving it
                                 # per geometry buys nothing.  It is the user's
                                 # number then, and the result says so
                                 # (`daxis_source`), because a reference nobody
                                 # can see is how γ ends up measured from the
                                 # wrong place.
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
    inc_ldq: bool = False,       # ALSO measure the incremental (differential)
                                 # d-q inductances by frozen permeability at a
                                 # few rotor positions of the reported window —
                                 # see `frozen_permeability_ldq`.  Costs one
                                 # extra linear back-solve triple per sampled
                                 # frame on a matrix the frame already has, and
                                 # nothing else about the run moves.
    six_phase: Optional[dict] = None,  # SIX-PHASE WINDING (two in-phase 3-phase
                                 # sets, ``winding_sets.resolve_six_phase``).  None
                                 # = 3 phases, byte-identical to before.  The sets
                                 # carry identical currents, so the source is the
                                 # 3-phase one (proved by column sum, reported);
                                 # with inc_ldq it also measures L_xy / L_dq per
                                 # set — see `frozen_permeability_vsd`.
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    geo_override: dict = None,
    eddy: bool = False,          # opt-in: time-coupled σ·∂A/∂t eddy-current solve
    eddy_method: Optional[str] = None,  # HOW the eddy steady state is reached.
                                 # "tdm" (the default since 2026-09-30): the
                                 # (anti)periodic orbit solved directly, all frames
                                 # of a (half) period at once (simulation/
                                 # time_periodic.py), then ONE period marched from
                                 # it and reported.  "march": BDF2 from a static
                                 # start, warm-up extensions until the settle gauge
                                 # is quiet.  None = SB_EDDY_METHOD, else the
                                 # config's simulation.eddy_method, else "tdm".
                                 # What TDM cannot serve (voltage / PWM drive,
                                 # series strand paths, a full ring, frozen ν, …)
                                 # or a TDM failure is marched, with a note in the
                                 # result (eddy_method_note).
                                 # docs/TDM_PROTOTYPE_2026-09-30.md
    tdm_demag: Optional[str] = None,  # TDM + demag: "full" (default) = the
                                 # march's one-period ratchet pre-pass, on the
                                 # orbit; "shortcut" = the owner's 1/6-period
                                 # window of the worst magnet, mapped to every
                                 # pole.  None = SB_TDM_DEMAG, else "full".
    rotor_eddy: bool = False,    # field-based magnet/shaft eddy losses (stranded coils)
    star_delta: str = None,      # TERMINAL connection of the three phases:
                                 # "star" (default) or "delta".  Orthogonal to
                                 # `connection`, which groups a phase's coils.
    strand_bonding: str = None,  # how the strands IN HAND are connected in the
                                 # coupled eddy solve: "transposed" (default,
                                 # one current row per strand) or "parallel"
                                 # (soldered ends — one U per turn, so the
                                 # circulating currents between the strands are
                                 # solved instead of assumed away)
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
    torque_filter: bool = False,  # deprecated compatibility option, ignored;
                                 # every reported torque sample is retained
    pole_copy: Optional[bool] = None,  # bit-identical pole/slot mesh; None=env default
    iron_template: Optional[bool] = None,  # deterministic template iron; None=env default
    geo_mesh: Optional[bool] = None,   # geometry-driven CDT mesh; None=env default
    progress_cb=None,            # optional callback(done:int, total:int) per frame
    magnet_scale: float = 1.0,   # scale ALL magnet Br (0 → PMs off = reluctance torque)
    magnet_temp_c: Optional[float] = None,  # MAGNET TEMPERATURE [°C].  None (every
                                 # caller until the EM-thermal coupling) = the assigned
                                 # magnet card is used exactly as the library quotes it,
                                 # so the numbers are bit-identical to what they have
                                 # always been.  A number corrects the card to it
                                 # (materials.MagnetMaterial.at_temperature): Br, the
                                 # normal coercivity AND the whole 2nd-quadrant curve —
                                 # the demag knee with it — move together, which is what
                                 # makes a magnet at 163 °C both weaker and closer to
                                 # irreversible loss than the same card quoted at 150 °C.
                                 # Not a torque knob: `magnet_scale` is that.
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
    structured_gap: bool = False,    # commercial-FEM-style concentric-ring air-gap mesh (experimental
                                     # Mesh-tab toggle; default off = free gmsh gap).
    airgap_macro: bool = False,      # harmonic air-gap macroelement (Mesh-tab "Harmonic gap"):
                                     # replaces the node re-pairing slip coupling with a smooth
                                     # analytic per-harmonic rotor↔stator link → RAW T(t) becomes
                                     # step-count independent (honest unfiltered ripple).  Works
                                     # on the full ring AND sector wedges (half-integer harmonic
                                     # ladder / skew-circulant for anti-periodic wedges).  ORs
                                     # with the SB_AIRGAP_MACRO env flag.
    drive: str = "current",          # EXCITATION SOURCE.  "current" = imposed sinusoidal phase
                                     # currents (default); "voltage" = imposed sinusoidal phase
                                     # VOLTAGE — the phase currents become circuit STATE solved
                                     # from V = R·i + dψ/dt each frame, so non-sinusoidal back-EMF
                                     # drives REAL parasitic harmonic currents (FOC-drive
                                     # verification mode); "pwm_voltage" = the SAME circuit driven
                                     # by an ideal two-level inverter's chopped pole voltages
                                     # (simulation/pwm.py), so the switching-frequency current
                                     # ripple — and the torque ripple / copper / core loss it
                                     # costs — is the machine's own response; "custom_current" =
                                     # an imposed ARBITRARY periodic phase-current waveform.
    v_phase_peak: float = 0.0,       # voltage drive: phase-voltage amplitude [V, peak].  Under
                                     # "pwm_voltage" this is the FUNDAMENTAL request — the
                                     # modulation index is m = 2·v_phase_peak/v_bus.
    v_delta_deg: float = 0.0,        # voltage drive: voltage angle [°el] in the SAME frame as γ
    v_bus: float = 0.0,              # pwm_voltage: DC link voltage [V] (pole voltage = ±v_bus/2)
    v_bus_real: float = 0.0,         # drive="inverter": the PHYSICAL DC link [V] when `v_bus`
                                     # above is the star-equivalent MODEL bus (√3·V_dc on a delta
                                     # machine).  Device drops and the dead-time clamp are computed
                                     # on this one — they are properties of the power stage, not of
                                     # the change of variable the delta machine is solved through.
                                     # 0 = there is no substitution and it IS `v_bus`.
    f_switch: float = 0.0,           # pwm_voltage: carrier frequency [Hz], SNAPPED to a whole
                                     # number of carriers per electrical period (synchronous PWM —
                                     # see simulation/pwm.py); the effective value is reported.
    waveform=None,                   # custom_current: [(θ_e_deg, i_A)] samples of the phase-A
                                     # TERMINAL current over one electrical period (B/C = the same
                                     # shape shifted ∓120°el), linearly interpolated.
    inverter_nonideal=None,          # drive="inverter" (Controller Stage 2): the device's own
                                     # non-ideality, as the Controller module resolved it —
                                     # {r_ds_ohm, v_sd_v0_V, v_sd_rd_ohm, dead_time_s, …} per LEG
                                     # at the solved junction temperature.  The bridge is then the
                                     # SAME modulator as "pwm_voltage" with the dead-time clamp and
                                     # the channel/diode drops on top, decided by the PREVIOUS
                                     # step's measured current (inverter/coupling.py).
    i_block: float = 0.0,            # bldc_current: FLAT-TOP terminal current of the 120° block
                                     # [A] — not an rms and not a sinusoid peak (rms = i_block·
                                     # √(2/3); see simulation/pwm.BlockCurrentSource).  γ is the
                                     # commutation advance, in the same frame as every other source.
    excitation: "Optional[_ExcSource]" = None,  # THE EXCITATION SOURCE OBJECT.  None (every
                                     # existing caller) = build it from `drive` and the keywords
                                     # above via excitation.make_source, so nothing changes.  Give
                                     # one and it replaces the whole five-drive family: the solver
                                     # asks it what mean voltage/current to apply over each step
                                     # (handing it the PREVIOUS converged step's currents and flux
                                     # linkages — a controller's sampling delay), what its smooth
                                     # fundamental is, how long its circuit state takes to settle,
                                     # and what the payload should report.  That is what lets an
                                     # EXTERNAL controller / co-simulation drive this machine with
                                     # saturation, the real back-EMF and the eddy reaction all in
                                     # the loop.  See simulation/excitation.ExcitationSource and
                                     # scripts/excitation_demo.py.
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
    slip_per_period: Optional[int] = None,  # force the slip-ring nodes per electrical
                                     # period (None = adaptive).  em_transient_eval's gap
                                     # refinement pins the first solve's ring so the rotor
                                     # angles (and the step snap) do not move.
    torque_method: Optional[str] = None,  # REPORTED torque: "coulomb" (DEFAULT since
                                     # 2026-09-30: Coulomb virtual-work waveform, mean and
                                     # ripple) or "hybrid_maxwell_ac" (energy / terminal-work
                                     # mean + raw Maxwell AC).  None = config
                                     # simulation.torque_method, else the default.  Both are
                                     # computed and stored either way.
    _tdm_ctl: Optional[Dict[str, Any]] = None,  # INTERNAL (fem_transient_sliding_band):
                                     # {"stop": "residual"} for a strict TDM retry, or
                                     # {"fallback": {"note", "attempts"}} on the march
                                     # that replaces a rejected TDM attempt.
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

    # ── STOP DURING SET-UP (2026-09-26) ──────────────────────────────────
    # The Stop button is honoured by the progress callback RAISING (the
    # route's _RunCancelled, a BaseException so no `except Exception` on the
    # way swallows it) — and the callback used to fire for the first time at
    # frame 0.  Everything before it — the d-axis calibration's own set-up,
    # the CAD polygons and the mesh, the per-tag assembly, the sliding-band
    # projections, the phasor initialiser's Picard, the static start field —
    # ran deaf: up to ~30 s after Stop on a large machine.  Each stage now
    # starts with a checkpoint.  It calls the callback with done = total =
    # None, which every progress consumer reads as "keep what you show"
    # (progress.ProgressTracker.update), so the bar does not move; a callback
    # written before the phase argument gets the two-argument form.  An
    # ordinary exception from a callback is not a cancel and is dropped, as
    # in the frame loop.
    def _cancel_point(stage: str) -> None:
        if progress_cb is None:
            return
        log.debug("set-up checkpoint: %s (%.2f s)", stage, _t.time() - t0)
        for _args in ((None, None, None, None), (None, None, None),
                      (None, None)):
            try:
                progress_cb(*_args)
                return
            except TypeError:
                continue
            except Exception:           # noqa: BLE001 — not a cancel
                return

    def _phase_point(text: str) -> None:
        """A cancel checkpoint that also NAMES the stage for the progress
        strip (the bar keeps its frame count) — the TDM orbit solve has no
        frames to count while it iterates."""
        if progress_cb is None:
            return
        for _args in ((None, None, text, None), (None, None, text),
                      (None, None)):
            try:
                progress_cb(*_args)
                return
            except TypeError:
                continue
            except Exception:           # noqa: BLE001 — not a cancel
                return

    _cancel_point("start")
    sampling_purpose = _sampling_purpose(sampling_purpose)
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
    torque_method = _resolve_torque_method(torque_method, sim)
    # Air-gap floor (sb_domains.effective_gap_layers; 1 = none unless the
    # SB_GAP_LAYERS_MIN fallback is set).  The measured gap rule lives in
    # em_transient_eval (Coulomb self-check gate + one refined re-solve).
    _gap_layers_req = float(gap_layers)
    gap_layers = _effective_gap_layers(gap_layers, sampling_purpose)
    _gap_layers_note = None
    if gap_layers != _gap_layers_req:
        _gap_layers_note = (
            "gap layers %g/side requested, %g/side used (floor %g/side)"
            % (_gap_layers_req, gap_layers, _GAP_LAYERS_MIN))
        log.warning("SB: %s", _gap_layers_note)
    # HOW the eddy steady state is reached (owner 2026-09-30: TDM for every
    # steady-state eddy run).  The argument, else SB_EDDY_METHOD, else the
    # config's simulation.eddy_method, else "tdm".  A TDM request the method
    # cannot serve (voltage / PWM, series strand paths, a full ring, …) or a
    # TDM solve that fails is MARCHED, with a one-line note in the result.
    import os as _os_em
    from motor_ai_sim.simulation.time_periodic import (
        resolve_eddy_method as _resolve_eddy_method, tdm_refusals as _tdm_refusals,
        resolve_tdm_demag as _resolve_tdm_demag)
    _eddy_method = _resolve_eddy_method(eddy_method, sim)
    _eddy_method_requested = _eddy_method
    # the march that replaces a rejected TDM attempt (fem_transient_sliding_band)
    _tdm_fb = (_tdm_ctl or {}).get("fallback")
    if _tdm_fb:
        _eddy_method_requested = "tdm"
    # The demag shortcut is EXPERIMENTAL: only the explicit argument selects it
    # (second Codex review, finding 11) — SB_TDM_DEMAG=shortcut is ignored with
    # a note, so no ordinary workflow can reach it through the environment.
    _tdm_demag, _tdm_demag_note = _resolve_tdm_demag(
        tdm_demag if tdm_demag is not None else _TDM_DEMAG_REQUEST.get())
    if _tdm_demag_note:
        log.warning("SB: %s", _tdm_demag_note)
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
        _elem_per_feat = 4.0 if hi_fidelity else 2.0   # normal: 2 elem/feature ceiling (÷4 hi-fi)
        _mesh_feat = mesh_feature_floor_mm(geo, min_size_mm, hi_fidelity)
        if _mesh_feat is not None:
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
    # geo mesh builds the 1/N wedge directly, but the sector ripple is still WIP.
    # This used to answer a 1/N request by silently solving the FULL RING —
    # "correct, just slower" — which meant the Mesh tab's 1/4 never actually ran
    # as 1/4 for anyone with the geo mesh on (the default): the user chose the
    # sector FOR ITS SPEED and paid full-disk time anyway, with one info log as
    # the only witness ("why does everything fall back to full", 2026-08-22).  A 1/N
    # request now falls back to the TEMPLATE wedge instead (geo mesh off for
    # this run): the sector the user asked for, on the validated wedge build —
    # the trade is the geo mesh's real fillets, which is the user's own speed/
    # fidelity choice expressed by picking 1/N.  SB_GEO_SECTOR=1 still opts
    # into the experimental geo-mesh wedge.
    _use_geo_tr = _SB_GEO_MESH if geo_mesh is None else bool(geo_mesh)
    _tpl_on = (iron_template is None and _SB_IRON_TEMPLATE) or bool(iron_template)
    _geo_mesh_eff = geo_mesh
    if (_use_geo_tr and _tpl_on and not _SB_GEO_SECTOR and not _full_ring):
        log.info("geo mesh: 1/%d requested — geo-mesh sector is WIP, using the "
                 "template wedge for this run (SB_GEO_SECTOR=1 forces the "
                 "experimental geo wedge)", int(n_sectors))
        _use_geo_tr = False
        _geo_mesh_eff = False
    NS = 1 if _full_ring else int(n_sectors)
    # Count divisibility alone does not certify the paired CAD geometry or
    # the actual phase terminals. Reject before calibration or mesh fallbacks.
    from motor_ai_sim.simulation.geometry_2d import validate_sector_symmetry
    validate_sector_symmetry(p.num_slots, p.num_poles, NS, dom.winding_layout,
                             paired_stator=True)
    sector_deg = 360.0 / NS
    pole_pairs = p.num_poles // 2
    # Sector boundary sign: ANTI-periodic (−1) only when the sector spans an
    # ODD number of poles (e.g. NS=4 → 7 poles); PERIODIC (+1) for an EVEN pole
    # count (NS=2 → 14 poles).  Mirrors the static solve's `anti_periodic =
    # (poles_per_sector % 2 == 1)`.  Hard-coding −1 here corrupted the 1/2-sector
    # field → 40 %-unbalanced phase-A flux linkage + 70 % torque ripple.
    _poles_per_sector = p.num_poles // NS
    _bc_sign = -1 if (_poles_per_sector % 2 == 1) else 1
    # PARALLEL PATHS from the connection label (4S / 2S-2P / 4P) — reported as
    # itself, never moved.
    n_parallel_conn = max(1, int(wind.get("n_parallel", 2) or 1))
    # STRANDS IN HAND (geometry.wire_parallel, default 1).  It divides the
    # SERIES turns of every coil without touching one physical wire, so
    # electrically it is a second parallel-path factor and NOTHING below this
    # line needs to know which of the two it came from: `n_parallel` is the
    # EFFECTIVE count from here on (I_coil = I_phase / n_parallel is then the
    # current in ONE STRAND, the MMF is turns_per_coil.I_coil, psi carries the
    # same divider, and R/Ld/Lq pick up 1/k^2 because both the turns and the
    # strand count move).  Raises — loudly, naming both numbers — when the
    # strands do not divide the wires; a rounded turn count is a machine the
    # user did not ask for.  See winding.wire_parallel_from_geo.
    from motor_ai_sim.winding import (turns_per_coil as _turns_per_coil,
                                      wire_parallel_from_geo as _wp_geo,
                                      wire_split_from_geo as _ws_geo,
                                      n_parallel_effective as _npar_eff)
    wire_parallel = _wp_geo(geo)
    # HOW THE STRANDS IN HAND ARE CONNECTED, for the coupled eddy solve.
    #
    #   strand_bonding="transposed"  -> one ∫J = I row per strand: every strand
    #       carries the same current whatever flux it links.  That is a
    #       PERFECTLY TRANSPOSED winding, and it is what this solver has always
    #       assumed (silently).
    #   strand_bonding="parallel"    -> the k strands of a TURN share ONE U, so
    #       their currents differ by the circulating term and sum to the turn
    #       current.  Soldering every turn is not how a coil is made, so this
    #       is the UPPER BOUND on the circulating loss, not the machine.
    #   strand_bonding="series"      -> the real thing: k STRAND PATHS, each
    #       running in series through every turn of the coil, joined only at
    #       the coil's two ends.  One current per PATH instead of one per
    #       strand-in-a-slot, and the k paths of a coil share the coil's
    #       terminal voltage.  This is neither bound — it is the answer they
    #       bracket (user 2026-09-11: "do it, we need to know for sure").
    #
    # `None` DERIVES it from the geometry, and that is the default because the
    # geometry already decides: `wire_parallel` = k wires in hand, and a coil
    # wound with k of them is soldered at its two ends — there is no third
    # possibility to offer (user 2026-09-11: "the connection of strands in
    # hand is chosen by our geometry, no need to make a selector").  k = 1 has nothing to
    # bond and lands on the per-strand rows either way.
    #
    # An explicit argument still overrides, because the two BOUNDS are what
    # make the middle number meaningful and a study has to be able to ask for
    # them: "transposed" for the lower one, "parallel" for the upper.
    #
    # THE ONE EXCEPTION is the voltage drive: the series path currents and the
    # terminal currents would have to be solved as one circuit, which
    # p2_drive.ve_newton does not do yet.  There the derivation falls back to
    # the transposed rows and SAYS SO — in the log and in the result's own
    # `strand_bonding` — rather than reporting an optimistic winding silently.
    _bond_s = str(strand_bonding or "").lower()
    if not _bond_s:
        _bond_s = "series" if int(wire_parallel) > 1 else "transposed"
        # `_vdrive` is derived from the excitation SOURCE further down; the
        # requested drive is what is knowable here and it is the same answer.
        if _bond_s == "series" and "volt" in str(drive or "").lower():
            log.warning(
                "strands in hand: %d wires, so the coil is soldered at its "
                "ends — but the series strand paths are not implemented on the "
                "VOLTAGE drive, so this run uses the transposed rows and its "
                "copper is the LOWER bound, not the machine", int(wire_parallel))
            _bond_s = "transposed"
    strand_group = int(wire_parallel) if _bond_s.startswith("par") else 1
    # k = 1 is NOT excluded, and that is deliberate: one strand in hand makes
    # every path a single conductor whose group row reads i_path = I_coil, i.e.
    # the transposed constraint written the long way round.  It is the only
    # exact test the augmented (A, U, i, W) system has — it must reproduce the
    # per-strand answer to solver precision — so it stays reachable.
    series_paths = _bond_s.startswith(("ser", "sold"))
    n_parallel = _npar_eff(n_parallel_conn, geo)
    # The connection LABEL this run is entitled to be reported under: only the
    # one whose parallel-path count matches the paths actually solved.  An
    # explicit n_parallel overrides the label (above), and a stale config label
    # left over from a previous selection would otherwise travel out with the
    # result and name the wrong winding on the summary card.  Matched against
    # the CONNECTION's own count: the strands in hand are not a connection and
    # must not disqualify a label that is telling the truth.
    _conn_label_used = ""
    try:
        from motor_ai_sim.winding import parse_connection as _pc_lbl
        _lbl = str(wind.get("connection") or "")
        if _lbl and int(_pc_lbl(_lbl)[0]) == int(n_parallel_conn):
            _conn_label_used = _lbl
    except Exception:
        _conn_label_used = ""
    # STRIPS PER WIRE ROW (geometry.wire_split, default 1).  The CAD lays N strips
    # of wire_width side by side per wire ROW, 2·wire_spacing_x apart, so the
    # slot holds num_wires_per_slot × N CONDUCTORS — each drawn, meshed and
    # current-constrained on its own.  `n_wires` below is that conductor count:
    # the ampere-turn multiplier the winding source, the per-conductor imposed
    # current and the J view are all normalised by, NOT the wire-row count.
    #
    # THE STRIPS OF A ROW ARE SERIES TURNS — always (the user removed the
    # parallel wiring on 2026-09-08: side-by-side strips link different leakage
    # flux, so a parallel connection would circulate current between them).  So
    # `n_parallel` above is NOT multiplied by N: every strip carries the full
    # branch current I/(paths·k), turns_per_coil is ×N, and therefore MMF ×N,
    # ψ ×N, R ×N², KV /N.  At N = 1 this is the unsplit machine and every
    # expression downstream is the one that was always there.
    wire_split = _ws_geo(geo)
    n_wires_drawn = int(geo.get("num_wires_per_slot", 14))
    n_wires = n_wires_drawn * wire_split
    turns_per_coil = _turns_per_coil(geo)
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
    # true q-axis and γ equals the physical current angle from the q-axis (=commercial FEM
    # el_deg).  Cached per topology; the I=0 calibration run is recursion-guarded.
    # …and it is REPORTED while it runs.  On a geometry the cache has not seen
    # it is a 24-frame no-load solve — measured 39 s on the 200 mm 24s/28p —
    # during which the progress bar showed nothing at all, so pressing Run
    # looked like pressing nothing.  It is not overhead to hide: it is what
    # gives the user's γ a zero to be measured from.
    _daxis_policy = None     # fix B: set only for optimizer candidates
    if daxis_deg is not None:
        if not (isinstance(daxis_deg, (int, float)) and math.isfinite(float(daxis_deg))):
            raise ValueError("daxis_deg must be a finite angle in degrees; got %r"
                             % (daxis_deg,))
        daxis_eff = float(daxis_deg) % 360.0
        _daxis_src = "manual"
        # A PIN FROM ANOTHER TOPOLOGY IS REFUSED (user 2026-09-03).  The panel
        # keeps DAXIS as a persisted field, and a duty snapshot / die record
        # can carry it onto a machine it was never measured on: the 24s/28p
        # family's 60° rode into a 12s/10p machine whose own d-axis is 120°,
        # so every γ the user typed sat 60° off the q-axis and a γ-sweep
        # "optimised" to 60° — the true optimum near 0°, seen through a wrong
        # zero.  When this topology has ALREADY been calibrated (memory or the
        # disk cache — no solve here), a manual pin more than 15° away from
        # that measurement is not a fine adjustment, it is the wrong machine's
        # number: refuse, name both, tell the user to clear the field.
        try:
            # Compare all topology fields, including the versioned resolved
            # winding, while retaining the existing cross-section pin policy.
            _kp = _daxis_topology_key(p, geo, wind, _daxis_geo_fingerprint(geo))[:-1]
            _known = [float(v) for k, v in _DAXIS_CACHE.items() if tuple(k[:-1]) == tuple(_kp)]
            _dp_chk = _daxis_disk_path()
            if _dp_chk and _os_sb.path.exists(_dp_chk):
                import json as _json_dx
                _pref = "_".join(str(x) for x in _kp) + "_"
                # A file that cannot be read, or is not a JSON object, is no
                # evidence; it must not also switch off the in-memory half of
                # this guard.
                try:
                    with open(_dp_chk) as _f_dx:
                        _disk_dx = _json_dx.load(_f_dx)
                except Exception:   # noqa: BLE001
                    _disk_dx = None
                for _k2, _v2 in (_disk_dx.items() if isinstance(_disk_dx, dict) else ()):
                    if (isinstance(_k2, str) and _k2.startswith(_pref)
                            and isinstance(_v2, (int, float)) and not isinstance(_v2, bool)
                            and math.isfinite(float(_v2))):
                        _known.append(float(_v2))
            if _known:
                _known.sort()
                _cal = _known[len(_known) // 2]
                _dev = abs((daxis_eff - _cal + 180.0) % 360.0 - 180.0)
                if _dev > 15.0:
                    raise ValueError(
                        "d-axis pin %.1f° does not belong to this machine: the %dp/%ds "
                        "%s winding calibrates to %.1f° (%d measurement%s on file), so "
                        "γ would sit %.0f° off the q-axis.  The pin was carried over "
                        "from another topology — clear the DAXIS field (empty = "
                        "measure) or enter a value within 15° of %.1f°."
                        % (daxis_eff, _kp[0], _kp[1], _kp[3] or "?", _cal, len(_known),
                           "" if len(_known) == 1 else "s", _dev, _cal))
        except ValueError:
            raise
        except Exception:   # noqa: BLE001 — the guard must never break a solve
            pass
    else:
        # Fix B: an optimizer candidate reuses its run's BASELINE d-axis when
        # its geometry change keeps both mirror symmetries (see
        # `_baseline_daxis_for_candidate`); otherwise — and for every
        # non-candidate solve, and inside a nested calibration — it measures
        # its own exactly as before.
        _base_dax = None
        if optimizer_candidate_active() and not getattr(_DAXIS_TLS, "calibrating", False):
            _base_dax, _daxis_policy = _baseline_daxis_for_candidate(
                geo, wind, n_sectors, progress_cb=progress_cb)
        if _base_dax is not None:
            daxis_eff = _base_dax
            _daxis_src = "baseline"
        else:
            daxis_eff = _resolve_daxis_shift(p, geo, wind, pole_pairs, geo_override,
                                             n_sectors, progress_cb=progress_cb)
            _daxis_src = "calibrated"

    _cancel_point("d-axis reference resolved")
    # Imposed excitation (both drives) — simulation/drive.py.  One object carries
    # the electrical frame both the current and the voltage waveform live in, so
    # they cannot drift apart.  It was written TWICE, once per element order, and
    # the two copies disagreed (see simulation/drive.py) — one definition now.
    # It survives here for ONE job: build_materials sizes the slot current
    # density before anything is solved, and a source that does not know what
    # its currents will be (every imposed-VOLTAGE one) has to hand it something.
    _exc = _Excitation(pole_pairs=pole_pairs, daxis_deg=daxis_eff,
                       i_peak=float(I_phase_rms) / n_parallel * math.sqrt(2),
                       gamma_deg=gamma_deg,
                       v_peak=float(v_phase_peak), v_delta_deg=v_delta_deg)

    # ── EXCITATION SOURCE ────────────────────────────────────────────────
    # ONE object decides everything about WHAT is applied, and the solver ASKS
    # it instead of branching on `drive` (simulation/excitation.py).  Two
    # families, split by `kind`, and that split is not a mode flag: an IMPOSED
    # CURRENT ('I' — the "current" sinusoid, the "custom_current" sampled
    # waveform, the "bldc_current" 120° block) enters the field problem as a
    # SOURCE TERM, while an IMPOSED VOLTAGE ('V' — the "voltage" sinusoid, the
    # "pwm_voltage" chopped inverter) makes the phase currents circuit UNKNOWNS
    # solved with the field by a bordered Newton.  Two different linear
    # algebras, which is why `kind` is the one thing branched on below.
    #
    # `excitation=` is how something that is none of the five drives this
    # machine: a co-simulated controller, a measured inverter log, a hardware
    # model-in-the-loop rig.  The factory runs only when nothing was handed in,
    # so every existing caller (and the route) is untouched — and the drive
    # names are still matched EXACTLY, not by prefix: a typo used to fall
    # through to the current drive and report a sinusoidal answer to a question
    # about an inverter.
    _src = excitation if excitation is not None else _make_source(
        drive, pole_pairs=pole_pairs, daxis_deg=daxis_eff,
        I_phase_rms=float(I_phase_rms), gamma_deg=gamma_deg,
        n_parallel=n_parallel, v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg), v_bus=float(v_bus),
        f_switch=float(f_switch), f_elec=float(f_elec),
        waveform=waveform, i_block=float(i_block),
        # Stage 2 only: the device's non-ideality and the two things the leg
        # current is reconstructed from (the connection and the model/real bus
        # pair).  Every other drive ignores them.
        inverter_nonideal=inverter_nonideal,
        star_delta=str(star_delta or "star"),
        v_bus_real=float(v_bus_real or 0.0))
    # What the payload calls this run's drive.  The SOURCE names itself, so a
    # hand-written one is reported as itself rather than as whatever `drive=`
    # happened to be left at.
    _drv_name = str(getattr(_src, "name", None) or drive or "current")
    # Voltage drive: imposed PHASE voltage in the same electrical frame as the
    # currents (v_delta_deg is directly comparable to γ), so a clean back-EMF
    # yields near-sinusoidal currents and a distorted one shows its real
    # parasitic harmonic currents + their losses.  Under PWM the SAME circuit is
    # driven by the inverter's pole voltages instead of a sinusoid, so every
    # settling/anchor/copper-from-solved-current path below applies unchanged —
    # only what V(t) is changes.
    _vdrive = (str(getattr(_src, "kind", "I")).strip().upper() == "V")
    # How long this source's circuit state takes to reach its orbit, and which
    # accelerators apply.  Everything the schedule below derives comes from it.
    _settle = _src.settle_policy()
    _conv_tol, _conv_cap = _sine_settle_criterion()
    _settle_static_effective, _settle_selection_reason = (
        _select_voltage_settle_periods(
            _settle.periods_static,
            sinusoidal_voltage=(type(_src) is _SineVoltageSource),
            eddy=bool(eddy), demag=bool(demag),
            explicit_override=bool(_SOURCE_V_SETTLE_ENV),
            converged_cap=_conv_cap))
    # The converged settle's state: None = a fixed count (every other source).
    _conv_settle = None
    if _settle_selection_reason == "plain_sinusoidal_voltage_converged_settle":
        _conv_settle = {"mode": "converged", "tol_rel": float(_conv_tol),
                        "cap_periods": int(_conv_cap), "converged": None,
                        "periods_used": None, "stop_residual": None,
                        "history": [], "last_anchor_period": 0,
                        "aitken_anchor": False}
        log.info("P2 plain sinusoidal voltage: settling until the period-to-"
                 "period change of I amplitude, T mean and T ripple is < %.3g "
                 "(cap %d periods); explicit SB_V_SETTLE_PERIODS still wins",
                 _conv_tol, _conv_cap)
    # ONE settling mechanism per run.  The converged settle marches until the
    # FREE orbit stops moving; the Δ² flux anchor jumps the state every third
    # period.  Together they never agree: on the real Ø40 L12 sine-voltage
    # point (2026-09-26 A/B) the anchor fired at periods 4, 7, …, 40, each
    # jump threw the ripple 8 % -> 39 % for a period, no clean pair ever fell
    # under 1e-3 and the run hit its 40-period cap (3.6x the wall) with the
    # reported window opening on the last anchor.  The anchor is therefore
    # OFF whenever the converged settle is on; every other source keeps its
    # policy unchanged.
    _aitken_on = bool(_settle.aitken) and _conv_settle is None
    # Carrier periods per electrical period; 0 = this source has no carrier.
    # The time-resolution gate, the mixed-settle geometry and the eddy gauge's
    # block width are all carrier questions, so they ask THIS rather than
    # "is the drive called pwm_voltage".
    _carriers = int(getattr(_src, "carriers", 0) or 0)
    # Whether the run's rms is the SOLVED / IMPOSED series rather than the
    # `I_phase_rms` argument (which such a source ignores entirely) — so the
    # copper loss has to be recomputed from what actually flowed.
    _rms_from_series = bool(getattr(_src, "rms_from_series", _vdrive))
    # What build_materials sizes the slot current density on.
    _nominal_currents = getattr(_src, "nominal_currents", None)
    if not callable(_nominal_currents):
        _nominal_currents = _exc.currents
    _desc0 = _src.describe() or {}          # static description (no run context)
    if _carriers:
        _mod = getattr(_src, "modulator", None)
        _pwm_v1 = tuple(getattr(_src, "v1_applied", (0.0, 0.0)))
        if _mod is not None:
            log.info("PWM inverter: V_bus=%.1f V, reference m=%.4f @ %.2f°el -> "
                     "applied V1=%.3f V pk @ %.2f°el (requested %.3f @ %.2f); "
                     "f_switch %.0f Hz -> %d carriers/period = %.0f Hz "
                     "(synchronous snap)",
                     _mod.v_bus, _mod.m, _mod.v_delta_deg,
                     _pwm_v1[0], _pwm_v1[1], float(v_phase_peak),
                     float(v_delta_deg), float(f_switch), _carriers,
                     _src.f_switch_eff_hz(f_elec))
    if _desc0.get("custom_current"):
        _cc0 = _desc0["custom_current"]
        log.info("custom current: %d samples, I_rms=%.2f A terminal "
                 "(I1=%.2f A rms at γ1=%.1f°el), %d parallel path(s)",
                 int(_cc0["n_samples"]), float(_cc0["I_phase_rms_A"]),
                 float(_cc0["I1_phase_rms_A"]), float(_cc0["gamma1_deg"]),
                 n_parallel)
    if _vdrive and rotor_eddy and not _SB_VDRIVE_ROTOR_EDDY:
        # ESCAPE HATCH ONLY (SB_VDRIVE_ROTOR_EDDY=0).  This is what every
        # imposed-voltage run used to do unconditionally: the eddy path imposes
        # coil currents via integral constraints while the voltage circuit needs
        # them as unknowns, so the conducting rotor was dropped and P_solid came
        # back as a zero that meant "not solved" — flattering the efficiency by
        # the magnet/shaft watts (measured: a PWM run read HIGHER efficiency
        # than its sinusoid reference purely because ~4 W fell off the books).
        # The two borders are merged now (p2_drive.ve_newton); this branch
        # exists to reproduce a pre-change number, nothing else.
        log.warning("voltage drive: rotor_eddy force-dropped by "
                    "SB_VDRIVE_ROTOR_EDDY=0 — running without conducting-rotor "
                    "dynamics (magnet/shaft eddy losses excluded; ΔP_harm = "
                    "copper+iron harmonic cost)")
        rotor_eddy = False
    # The conducting rotor rides on the COUPLED σ·∂A/∂t solve — under an imposed
    # voltage there is no second route: the frequency-domain post-process
    # (honest_rotor_eddy) needs a rotor-frame A(t) history, which exists either
    # way, but the SCREENING the eddy currents apply back onto the field (and
    # therefore onto the solved terminal current) only exists inside the Newton.
    # eddy=False + rotor_eddy=True under voltage drive is therefore the
    # post-processed estimate, exactly as it is under current drive.
    _vd_rotor_eddy = bool(_vdrive and rotor_eddy)

    # ``_src.fundamental`` is what the dq PHASOR INITIALISER samples to place
    # the currents on their steady-state orbit before the march starts.  Under
    # PWM that is the MODULATING SINUSOID, not the chopped waveform: the
    # initialiser solves a fundamental-frequency phasor problem, in which a
    # square wave has no meaning.  The switching content is a perturbation on
    # that orbit and is resolved by the marched frames (which sample the
    # inverter through ``_src.mean_over``), not by the initialiser.
    #
    # ``_src.mean_over(fb)`` is the value the frame loop applies over ONE step,
    # and it is one call for all five sources now instead of a branch:
    #
    #   sinusoidal voltage — the midpoint sample, which is the Crank–Nicolson
    #     rule and what every pinned voltage-drive number was produced by;
    #   PWM, fine frames — the EXACT mean over the step's rotor motion.  The
    #     circuit residual multiplies V by Δt_k, so V·Δt_k is ∫v dt over the
    #     step and the interval mean is the quantity that integral wants.  A
    #     midpoint sample of a two-level waveform is ±v_bus/2 chosen by where
    #     the step happened to land between edges — aliasing, and CN would ring
    #     on it at Nyquist.  With the exact mean, too few steps per switching
    #     period UNDER-resolve the ripple (the answer walks back toward the
    #     sinusoid) rather than inventing one; that is the honest failure
    #     direction, and the caller is told the ratio;
    #   PWM, coarse settle frames (fb.fine False) — the midpoint sample of the
    #     modulator's own fundamental, i.e. the amplitude and angle it was
    #     compensated to APPLY, so the settle marches the same orbit the fine
    #     window will land on, minus the ripple;
    #   imposed current — the current at the frame's angle, which is a
    #     constraint the field solve satisfies there rather than an integral.

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
    # is unchanged and the 6·k physical ripple already matches commercial FEM.  gap_layers
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
    if slip_per_period or _SLIP_PER_PERIOD_OVERRIDE:  # force ring density (dev flag —
        # decouples ring-count/mesh convergence studies from the adaptive
        # slip(gap_layers) coupling).  Snapped UP to the 24k grid so the wedge
        # node counts (n_slip/n_sectors) stay integral for sector models too.
        _spo = int(slip_per_period or _SLIP_PER_PERIOD_OVERRIDE)
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
    # ── FINE STEPS ARE LEGAL: raise the ring instead of capping the caller ──
    # The snap below picks the nearest DIVISOR of the ring, so the ring count is
    # also a hard CEILING on the time resolution: ask for more steps than there
    # are slip nodes in a period and the run silently drops to the ring count.
    # For a PWM study that ceiling is exactly the wrong constraint — resolving a
    # 48 kHz carrier on a 1.5 kHz fundamental needs ~500 steps/period against a
    # default ring of 240, and the run would have reported switching physics it
    # never resolved.  So when (and ONLY when) the request is above the ceiling,
    # the RING follows the steps: the node count is raised to the smallest
    # multiple of the requested steps that is at least the adaptive density, and
    # kept divisible by n_sectors so the wedge ring stays integral.
    #
    # Deliberately NOT applied below the ceiling.  A request that is merely a
    # non-divisor (36 on a 240 ring) still snaps to the nearest divisor, because
    # raising the ring there would change the mesh — and with it the torque and
    # the ripple floor — of every existing run that asked for a non-divisor.
    # Above the ceiling there is no such history: the run was impossible before.
    if (not _macro_free_m) and _req_steps > _nodes_per_period:
        _ns_abs2 = max(1, abs(int(n_sectors)))
        _spp2 = int(_req_steps) * max(
            1, int(math.ceil(_nodes_per_period / float(_req_steps))))
        while (pole_pairs * _spp2) % _ns_abs2:
            _spp2 += int(_req_steps)
        log.warning("SB: %d steps/period requested above the %d-node slip ring "
                    "— RAISING the ring to %d nodes/period (%d on the full "
                    "circle) so the requested time resolution is honoured "
                    "exactly; the band mesh is correspondingly denser and the "
                    "run is slower",
                    _req_steps, _nodes_per_period, _spp2,
                    pole_pairs * _spp2)
        _slip_per_period = _spp2
        _nodes_per_period = _spp2
        n_slip_eff = pole_pairs * _spp2
    if _macro_free_m:
        n_steps_per_period = max(1, _req_steps)
    else:
        n_steps_per_period = _snap_steps_to_nodes(_req_steps, _nodes_per_period)
        # PWM: never snap DOWN.  The nearest-divisor rule turned an approved
        # request into fewer samples per carrier than the resolution gate had
        # just checked (measured: guard suggests 176, snap delivered 120 —
        # 10.9 samples/carrier instead of 16).  Rounding UP to the smallest
        # ring divisor >= the request keeps the ring untouched and the
        # resolution at least what was asked; there is no legacy to preserve
        # here, the PWM drive is new.
        if _carriers and n_steps_per_period < _req_steps:
            _up = [d for d in range(_req_steps, _nodes_per_period + 1)
                   if _nodes_per_period % d == 0]
            if _up:
                n_steps_per_period = _up[0]
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
                        "torque); cogging minimum is checked next",
                        _req_steps, n_steps_per_period, _nodes_per_period)
    _cogging_sampling = _cogging_frame_policy(
        int(p.num_slots), int(p.num_poles), int(pole_pairs),
        int(n_steps_per_period), int(_nodes_per_period),
        continuous_angle=_macro_free_m,
        sampling_purpose=sampling_purpose,
        internal_daxis_calibration=bool(
            getattr(_DAXIS_TLS, "calibrating", False)))
    n_steps_per_period = _cogging_sampling["steps_per_period"]
    if _cogging_sampling["auto_raised"]:
        log.warning("SB: cogging resolution raised to %d raw frames/period "
                    "(requested %d, %d cycles/period, %d raw samples/cycle, "
                    "minimum %d frames); "
                    "using %s without changing the slip ring",
                    n_steps_per_period, _req_steps,
                    _cogging_sampling["cycles_per_electrical_period"],
                    _cogging_sampling["target_raw_samples_per_cycle"],
                    _cogging_sampling["min_required_steps_per_period"],
                    "continuous angles" if _macro_free_m else
                    "an existing whole-node divisor")
    elif _cogging_sampling["reason"] == "requested_steps_kept_below_cogging_target":
        # Standard / optimization run below the cogging target: the requested
        # resolution is kept (68de0ca) and the RECORD says the cogging/ripple
        # content is under-sampled. Opt in with sampling_purpose=
        # "cogging_quality" for a cogging-grade waveform.
        log.warning("SB: cogging angle resolution below target: %d raw "
                    "frames/period for %d cogging cycles/period (%.3f "
                    "samples/cycle, target %d for purpose %r) — requested "
                    "resolution kept; ripple/cogging may be aliased. Use "
                    "sampling_purpose='cogging_quality' for >= 6 samples/cycle",
                    n_steps_per_period,
                    _cogging_sampling["cycles_per_electrical_period"],
                    _cogging_sampling["raw_samples_per_cycle"],
                    _cogging_sampling["target_raw_samples_per_cycle"],
                    sampling_purpose)
    elif (not _cogging_sampling["sufficient"] and
          _cogging_sampling["reason"] not in ("internal_daxis_calibration_exempt",
                                              "internal_probe_exempt")):
        log.warning("SB: INSUFFICIENT cogging angle resolution: %d raw "
                    "frames/period for %d cycles/period (%.3f samples/cycle; "
                    "target %d). The existing %d-node/period slip ring has "
                    "no admissible divisor; all samples are retained",
                    n_steps_per_period,
                    _cogging_sampling["cycles_per_electrical_period"],
                    _cogging_sampling["raw_samples_per_cycle"],
                    _cogging_sampling["target_raw_samples_per_cycle"],
                    _nodes_per_period)
    # Steps per SWITCHING period is the number that decides whether a PWM run
    # measured the ripple it reports.  The exact-mean voltage integration makes
    # an under-resolved run fail SAFE (it averages toward the sinusoid) rather
    # than alias, so this is a warning about an UNDER-estimate, not about
    # garbage — but an under-estimate presented without saying so is exactly the
    # way a PWM study concludes "the ripple is small".
    # ── A SOURCE WHOSE WAVEFORM DEPENDS ON THE TIME STEP ─────────────────
    # Finished here, not with the other sources, because a one-step-wide
    # feature is only known once the step count has been through the slip-node
    # snap above.  The BLDC block is the one that has one: an ideal block's
    # infinite di/dt is not a source a time-marched solver can accept — the
    # whole step would land inside one frame and the reported dψ/dt would be an
    # artifact of the frame size — so its edges ramp over exactly one step and a
    # finer run gives a sharper edge, not a different machine.  Every other
    # source hands itself back.
    _snap_hook = getattr(_src, "on_steps_snapped", None)
    if callable(_snap_hook):
        _src = _snap_hook(int(n_steps_per_period)) or _src
        _nominal_currents = getattr(_src, "nominal_currents", _nominal_currents)
        _desc0 = _src.describe() or {}
    if _desc0.get("bldc"):
        _bd0 = _desc0["bldc"]
        log.info("BLDC 120° block: i_block=%.2f A flat top -> %.2f A rms "
                 "terminal (I1=%.2f A rms at γ1=%.1f°el), commutation ramp "
                 "%.2f°el = 1 time step, %d parallel path(s)",
                 float(_bd0["i_block_A"]), float(_bd0["I_phase_rms_A"]),
                 float(_bd0["I1_phase_rms_A"]), float(_bd0["gamma1_deg"]),
                 float(_bd0["commutation_ramp_deg"]), n_parallel)
    # ── STEPS PER SWITCHING PERIOD — a HARD requirement, not advice ──────
    # The exact-mean voltage integration makes an under-resolved PWM run fail
    # SAFE: it averages the pulses instead of aliasing, so the answer walks back
    # toward the ideal sinusoid.  That is exactly why it must not be allowed to
    # run quietly — at dt >= T_switch the switching effect VANISHES and the run
    # reproduces the sinusoid at PWM prices, a wrong answer wearing the right
    # label.  Below 4 samples per switching period the run is REFUSED (2 is
    # the Nyquist edge of the carrier-frequency ripple itself — alignment
    # decides what survives); 4..8 runs as a declared COARSE first pass, 8..16
    # runs with the mild note.  4 was the user's own floor (2026-08-31) and
    # the coarse-pass error is MEASURED, not guessed — see the calibration
    # table in the log line below.
    if _carriers:
        _nc_sw = int(_carriers)
        _sp_sw = float(n_steps_per_period) / float(_nc_sw)
        if _sp_sw < 4.0:
            from motor_ai_sim.simulation.pwm import ExcitationError as _ExcE3
            raise _ExcE3(
                "PWM is under-resolved: %d steps/period over %d carriers is "
                "%.1f time steps per switching period (f_switch %.4g Hz -> "
                "%.4g Hz effective against f_elec %.4g Hz).  Below 4 the "
                "carrier ripple is at its Nyquist edge and averages out — the "
                "run reproduces the ideal sinusoid at PWM cost.  Set at least "
                "%d steps/period for a coarse first pass (4/carrier), %d for "
                "a fully resolved 16 per carrier, or lower f_switch."
                % (int(n_steps_per_period), _nc_sw, _sp_sw, float(f_switch),
                   _src.f_switch_eff_hz(f_elec), f_elec,
                   4 * _nc_sw, 16 * _nc_sw))
        if _sp_sw < 8.0:
            log.warning("PWM COARSE first pass: %.1f time steps per switching "
                        "period (%d steps / %d carriers) — the switching "
                        "effect shows and the torque is already right, but "
                        "the ripple-driven numbers read LOW (measured, 40 mm "
                        "at 16.7 kHz, 4.4/carrier vs 21.8: torque -0.1 %%, "
                        "torque ripple -15 %%, copper -4 %%, iron -20 %%).  "
                        "%d steps/period resolves it fully.",
                        _sp_sw, n_steps_per_period, _nc_sw, 16 * _nc_sw)
        elif _sp_sw < 16.0:
            log.warning("PWM: %.1f time steps per switching period (%d steps "
                        "/ %d carriers) — below 16 the switching ripple is "
                        "partly averaged out and the reported torque ripple, "
                        "copper and core loss are UNDER-estimates.  %d "
                        "steps/period resolves it fully.",
                        _sp_sw, n_steps_per_period, _nc_sw, 16 * _nc_sw)

    # ── Build the two halves ONCE ────────────────────────────────────────
    _cancel_point("CAD polygons")
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
    # NOMINAL section = every STRIP's rectangle.  A strip is wire_width ×
    # wire_height and there are num_wires × wire_split of them per slot, so the
    # copper the parameters describe grows with the split exactly as the drawn
    # copper does — the "did the CAD deliver it" check stays a comparison of the
    # same two machines.
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
    # ── Conductor SKIN LAYER (docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24)
    # The shaft's eddy current flows in δ = sqrt(2/(ωμσ)) under its OD; the
    # CDT wall cells are 5-50 δ thick, which reads the shaft loss low.  When
    # the rotor conductors are solved (rotor_eddy), size a structured layered
    # wall on δ at the highest rotor-frame frequency the duty drives (slot
    # passing, or the PWM carrier) and the steel's largest μ.
    _skin = None
    _skin_info = None
    if rotor_eddy:
        from motor_ai_sim.simulation.conductor_skin import (
            skin_layer_enabled as _sk_on, shaft_skin_spec as _sk_spec,
            rotor_frame_ref_hz as _sk_f, sleeve_layers as _sk_sl)
        if _sk_on():
            try:
                _sig_sk, _mu_sk = _shaft_skin_material()
                _f_sk = _sk_f(int(p.num_slots), float(rpm),
                              float(f_switch) if _carriers else None)
                _r_sk = float(motor.parameters.get("rotor_inner_radius") or 0.0)
                _sp = _sk_spec(_sig_sk, _mu_sk, _f_sk, _r_sk, int(p.num_slots),
                               int(pole_pairs))
                if _sp is not None:
                    _skin = {"shaft": _sp}
                    _skin_info = {k: (round(float(v), 6)
                                      if isinstance(v, (int, float)) else v)
                                  for k, v in _sp.items()}
                    log.info("shaft skin layer: delta=%.4g mm at %.4g Hz "
                             "(sigma %.3g S/m, mu_r,max %.0f) -> h1 %.4g mm, "
                             "growth %.3g, chord %.3g mm",
                             _sp["delta_mm"], _f_sk, _sig_sk, _mu_sk,
                             _sp["h1_mm"], _sp["growth"], _sp["chord_mm"])
            except Exception as _sk_e:   # noqa: BLE001 — loud, never silent
                log.warning("shaft skin layer spec failed (%s: %s) — the shaft "
                            "wall is meshed without it", type(_sk_e).__name__,
                            _sk_e)
            _nsl = _sk_sl()
            if _nsl:
                _skin = dict(_skin or {})
                _skin["sleeve"] = {"layers": float(_nsl)}
    _cancel_point("mesh")
    ms, ts, cs, mr, tr, cr = _build_sliding_band_meshes(
        polys, 0.0, mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, band_thickness_mm=0.4,
        n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0,
        gap_layers=gap_layers,
        component_mesh_mm=component_mesh_mm,
        full_ring=_full_ring, pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=_geo_mesh_eff,
        skin_layers=_skin)
    # Build provenance — captured IMMEDIATELY after the build (thread-local in
    # the mesher, so a later build on this thread would overwrite it).  Reported
    # in the result dict: a fallback-built mesh moves the ripple noise floor by
    # percentage points, so the consumer (optimizer, cache) must be able to see
    # that this run did not get the build it requested.
    _build_prov = _mesh_build_trace()
    if _build_prov["events"]:
        log.warning("mesh build DEGRADED (%d fallback(s)): %s",
                    len(_build_prov["events"]),
                    " | ".join(_build_prov["events"]))
    if _build_prov.get("notes"):
        log.info("mesh build path: %s", " | ".join(_build_prov["notes"]))
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
    # ── WHERE THE SLIP SURFACE ACTUALLY IS ──────────────────────────────
    # `mid` was computed from the RADII (0.5·(r_rotor_out + r_stator_in)) long
    # before the polygons existed.  The polygons are what the two halves were
    # CUT at, and with a retaining sleeve on the rotor OD they put the slip
    # circle in the middle of the MECHANICAL gap (sleeve OD .. bore), not of the
    # magnetic one — so the radii formula names a circle the stator half has no
    # nodes on.  Measured on the 30 mm at air_gap 1.0 with a 0.4 mm sleeve: the
    # ring search at 8.7 mm found ZERO stator nodes (the polygons cut at
    # 8.9 mm), the two halves were never coupled, the stator solved to A ≡ 0,
    # and psi_A came back as 24 exact zeros — which the d-axis calibration then
    # refused, correctly, as "no interior maximum".  A silent decoupling is
    # exactly the failure this codebase refuses to ship, so read the number back
    # from the geometry that produced the meshes.
    #
    # WITHOUT a sleeve the two expressions are the same number, and the guard
    # below (strictly greater) keeps the old value bit-for-bit.
    _mid_poly = polys.get("mid_r_mm")
    if _mid_poly is not None:
        try:
            _mid_m = float(_mid_poly) * 1e-3
            if _mid_m > mid + 1e-12:
                log.info("slip surface: %.4f mm from the built polygons "
                         "(radii formula says %.4f mm — a retaining sleeve "
                         "moved the mechanical gap)", _mid_m * 1e3, mid * 1e3)
                mid = _mid_m
        except (TypeError, ValueError):
            pass
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

    _cancel_point("materials and per-tag assembly")
    # ── Pre-assemble per-tag stiffness K0 + constant magnet source ───────
    # The ROTOR half's material map — the one the magnet domains come out of, so
    # this is the single build that carries `magnet_temp_c`.  The three stator
    # builds below read coil J_z only; handing them the temperature would change
    # nothing but the log.
    matr0 = build_materials(_nominal_currents(0.0), dom.winding_layout,
                            getattr(cr, "polys", polys), 0.0, slot_area_m2, n_wires,
                            magnet_temp_c=magnet_temp_c)
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
    # The per-request override (?mat= / set_request_materials) merges here with
    # the SAME precedence build_materials gives the B-H side.  Without it the
    # loss surface, magnet σ and shaft σ came from the SHARED config while the
    # field was solved with the USER's steel — P_fe describing a different
    # machine than the torque (the F6 failure mode, measured at +90 % P_fe).
    try:
        from motor_ai_sim.material_context import get_request_materials as _grm
        _ov_ctx = _grm() or {}
    except Exception:
        _ov_ctx = {}
    _ov_props = _ov_ctx.get("materials") or {}
    _ov_asg = _ov_ctx.get("assignment") or {}
    if _ov_asg:
        _ma = {**_ma, **{k: v for k, v in _ov_asg.items() if v}}

    def _lib_mat(cat: str, name: str):
        """Override-carried props first, else the library — mirrors
        build_materials._resolve_mat so a user's custom steel keeps its own
        loss curves instead of silently borrowing the library's."""
        if name and name in _ov_props:
            try:
                _c = (_ov_props[name] or {}).get("category") or cat
                return _mat_lib.material_from_dict(_c, name, _ov_props[name])
            except Exception:
                pass
        return _mat_lib.get_material(cat, name)

    try:
        _steel_s = _lib_mat("steel", _ma.get("stator_core", "20SW1200"))
    except Exception:
        _steel_s = None
    try:
        _steel_r = _lib_mat("steel", _ma.get("rotor_core", "20SW1200"))
    except Exception:
        _steel_r = None
    try:
        _magnet_mat = _lib_mat("magnet", _ma.get("magnet")) if _ma.get("magnet") else None
    except Exception:
        _magnet_mat = None

    def _lookup_sigma(name: str) -> float:
        # `insulator` is last so no existing name can change category: copper,
        # the steels and the magnets all resolve before it is reached.  It is in
        # the list at all because the retaining sleeve's material lives there.
        for _cat in ("conductor", "steel", "magnet", "insulator"):
            try:
                return float(getattr(_lib_mat(_cat, name), "sigma", 0.0) or 0.0)
            except Exception:
                continue
        return 0.0

    _sigma_mag_lib = (float(getattr(_magnet_mat, "sigma", 0.0) or 0.0)
                      if _magnet_mat else 0.0) or SIGMA_NDFEB
    _sigma_shaft_lib = _lookup_sigma(str(_ma.get("shaft", ""))) or SIGMA_SHAFT
    # The sleeve's conductivity, from its assigned material (default
    # T800_UD_60) — the TRANSVERSE value, see SIGMA_SLEEVE.
    _sleeve_mat_name = str(_ma.get("sleeve", "") or "")
    if not _sleeve_mat_name:
        try:
            from motor_ai_sim.materials import DEFAULT_PART_MATERIAL as _DPM2
            _sleeve_mat_name = _DPM2.get("sleeve", "")
        except Exception:      # noqa: BLE001
            _sleeve_mat_name = ""
    _sigma_sleeve_lib = _lookup_sigma(_sleeve_mat_name) or SIGMA_SLEEVE

    # An EXCLUDED part is air: mu_r = 1 AND sigma = 0.  build_materials already
    # made it non-magnetic; the eddy mass matrix is assembled from THIS map, so
    # without the same switch here an "excluded" shaft would still carry solid
    # eddy currents and report a P_shaft_eddy_W — air that dissipates.
    _ex_parts = _excluded_parts()

    def _sigma_of_tag(t: int) -> float:
        t = int(t)
        if t >= DOM_COIL_BASE or t == DOM_COIL:
            return 0.0 if "slot" in _ex_parts else _sig_cu_T
        if t >= DOM_MAG_BASE or t in (DOM_MAG_N, DOM_MAG_S):
            return 0.0 if "magnet" in _ex_parts else _sigma_mag_lib
        if t == DOM_SHAFT:
            return 0.0 if "shaft" in _ex_parts else _sigma_shaft_lib
        if t == DOM_SLEEVE:
            return 0.0 if "sleeve" in _ex_parts else _sigma_sleeve_lib
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
    mats_full = build_materials(_nominal_currents(0.0), dom.winding_layout,
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

    _cancel_point("eddy conductor data")
    # ── Stage 2: solid-copper current-constrained eddy data ──────────────────
    # Each coil is a SOLID bar: J = σ(−∂A/∂t + U_c) with ∫J dA = I_c imposed.
    # Per coil store: g_c (σ-lumped load, full DOF space), S_c = ∫σ dA, and the
    # imposed-current coefficient I_c_unit = dir for EACH physical conductor.
    # `Ist` is already divided by n_parallel_effective (connection paths ×
    # strands in hand), so every meshed strip gets its actual branch current;
    # wire_split strips are series turns and do not divide it. The slot's
    # conductor count is `n_wires`, so summing these per-body currents gives
    # the same total ampere-turns as the area-normalized magnetostatic J_z.
    # Do not scale this current by each tag's meshed area: that would make
    # series turns carry different currents solely because their triangles
    # differ slightly in area, and k=1 series would disagree with transposed.
    # The area normalization remains in build_materials for the continuum
    # source; eddy constraints instead express physical conductor currents.
    _coil_con = []
    _eddy_con_check = None
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
            # "coil_<i>_slot<j>_<phase><+|->" — j identifies the SLOT, and
            # build_winding_layout puts a single-layer coil's two sides in
            # slots 2c and 2c+1, so c = j//2 names the physical COIL.  Nothing
            # else in this solver needed to know which conductor belongs to
            # which coil; the series strand paths do.
            try:
                _slot_j = int(nm.split("_slot", 1)[1].split("_", 1)[0])
            except Exception:
                _slot_j = -1
            _coil_con.append({
                "tag": int(tag),          # domain tag — the P2 branch rebuilds g/S
                                          # on ITS basis and needs the identity of
                                          # the wire this (phase, Iunit) belongs to.
                "g": np.concatenate([g_s, np.zeros(_nr0)]),
                "S": float(g_s.sum()),
                "Iunit": dr,
                "phase": ph,
                "slot": _slot_j,
                "coil": (_slot_j // 2 if _slot_j >= 0 else -1),
                "nodes": np.unique(half["s"]["mesh"].t[:, idx]),   # stator-local node ids
            })
        # LOUD GUARD (2026-09-23): with Iunit = ±1 per body the slot's
        # ampere-turns are right only if the mesh holds exactly n_wires bodies
        # per slot — checked here, once, on the bodies both element orders
        # read their currents off.  A sector model must hold whole slots.
        _eddy_con_check = check_eddy_conductor_bodies(
            _coil_con, _coil_area_meshed, n_wires,
            n_slots_expected=(int(p.num_slots) // int(NS)
                              if int(p.num_slots) % int(NS) == 0 else None),
            log=log)

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
    # An EXCLUDED core is air, and air has no Bertotti loss.  Emptying the
    # element set is what makes P_fe exactly 0 for it — build_materials only
    # took the iron's PERMEABILITY away, and the loss integral runs over
    # element indices, not over mu.
    if "stator_core" in _ex_parts:
        _iron_s_idx = np.array([], int)
    if "rotor_core" in _ex_parts:
        _iron_r_idx = np.array([], int)
    _mag_parts = []
    for _tag, _idx in half["r"]["cells"].items():
        _m = matr0.get(int(_tag))
        if _m is not None and (abs(_m.Mx) + abs(_m.My)) > 0:
            _mag_parts.append(np.asarray(_idx, int))
    _mag_idx = np.concatenate(_mag_parts) if _mag_parts else np.array([], int)

    # ── the AIR GAP, as an element set ──────────────────────────────────────
    #
    # User 2026-09-10: *"for the electromagnetic analysis we also need to
    # compute the mean field in the gap and write that number into the table"*.  The mean |B| over
    # the clearance is the number a machine is sized on before anything else,
    # and it is the one quantity the summary never carried.
    #
    # NOT by domain tag alone.  gmsh paints the rotor half's leftover area with
    # DOM_AIRGAP as its DEFAULT (see mesher, "the gap built" trace), so that tag
    # reaches well past the clearance and its mean would be a mean of mostly
    # bore air.  The set is therefore cut to the annulus between the outermost
    # ROTATING metal (iron, magnets, band) and the stator bore — measured off
    # the mesh rather than off the geometry parameters, so a sleeve, a stepped
    # pole or a chamfer moves it without anyone remembering to.
    # NB `_t` is this module's alias for the `time` module — never a loop
    # variable in here (it was, for one commit, and every solve died in the
    # d-axis calibration with "'int' object has no attribute 'time'").
    def _cells_of(_hk, _dtags):
        _c = half[_hk]["cells"]
        _got = [np.asarray(_c.get(int(_dt), np.array([], int)), int)
                for _dt in _dtags]
        _got = [a for a in _got if a.size]
        return np.concatenate(_got) if _got else np.array([], int)

    def _node_r(_hk, _idx):
        _hm = half[_hk]["mesh"]
        if not _idx.size:
            return np.array([], float)
        _tri3 = np.asarray(_hm.t)[:, _idx]
        return np.hypot(np.asarray(_hm.p)[0][_tri3],
                        np.asarray(_hm.p)[1][_tri3])

    def _cen_r(_hk, _idx):
        _hm = half[_hk]["mesh"]
        if not _idx.size:
            return np.array([], float)
        _tri3 = np.asarray(_hm.t)[:, _idx]
        return np.hypot(np.asarray(_hm.p)[0][_tri3].mean(axis=0),
                        np.asarray(_hm.p)[1][_tri3].mean(axis=0))

    _r_rot_max = 0.0
    for _dt in (DOM_ROTOR, DOM_SLEEVE, DOM_MAG_N, DOM_MAG_S):
        _rr = _node_r("r", _cells_of("r", (_dt,)))
        if _rr.size:
            _r_rot_max = max(_r_rot_max, float(_rr.max()))
    for _tag, _idx in half["r"]["cells"].items():
        if int(_tag) >= int(DOM_MAG_BASE):
            _rr = _node_r("r", np.asarray(_idx, int))
            if _rr.size:
                _r_rot_max = max(_r_rot_max, float(_rr.max()))
    _rs = _node_r("s", _cells_of("s", (DOM_STATOR,)))
    _r_sta_min = float(_rs.min()) if _rs.size else 0.0

    _gap_s_idx = np.array([], int)
    _gap_r_idx = np.array([], int)
    if _r_sta_min > _r_rot_max > 0.0:
        for _hk, _name in (("s", "_gap_s_idx"), ("r", "_gap_r_idx")):
            _idx = _cells_of(_hk, (DOM_AIRGAP, DOM_BAND))
            if _idx.size:
                _rc = _cen_r(_hk, _idx)
                _idx = _idx[(_rc > _r_rot_max) & (_rc < _r_sta_min)]
            if _hk == "s":
                _gap_s_idx = _idx
            else:
                _gap_r_idx = _idx
    else:
        log.debug("air-gap mean |B|: no clearance annulus found "
                  "(rotor OD %.6f m, stator bore %.6f m)",
                  _r_rot_max, _r_sta_min)
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
    _cancel_point("warm-start and demag seeds")
    # ── The seed's identity, and the Br half of it ───────────────────────────
    # `_wmeta` is built HERE rather than beside the eddy-history seed further
    # down, because the Br map has to be in `_br_glob` BEFORE the magnet source
    # `_mx_all/_my_all` is assembled from it — and the two halves of a seed must
    # be judged by ONE key, or a run could continue a magnet whose field it
    # refused.  Identical to the value the old site computed: `n_periods` is
    # only ever raised afterwards on the voltage (`_vskip`) and the NON-eddy
    # demag (`_dmskip`) paths, and the warm cache is published and read on the
    # eddy current-drive path alone.
    #
    # "conn" is the RESOLVED effective parallel-path count, not the label the
    # caller passed: the interactive Simulation run that is meant to be the
    # "once" of a sweep (user 2026-09-06) usually passes connection=None while
    # a sweep eval passes None too — but a machine loaded with an explicit
    # label would otherwise never match its own sweep, and the seed would be
    # silently refused for a difference that is not a difference.
    _geo_fp = _geo_fingerprint(geo)
    _wmeta = {"nspp": int(n_steps_per_period), "npd": float(n_periods),
              "conn": "np%d" % int(n_parallel),
              "temp": round(float(coil_temp_c), 1),
              "mscale": float(magnet_scale)}
    # The eddy TIME SCHEME is part of the discretisation too: a backward-Euler
    # state (and its same-angle loss reference, ~10 % lower on the L155
    # magnets) is not the BDF2 orbit.  Only the BDF2 key carries it, so a
    # backward-Euler run (SB_EDDY_BE=1) reads and writes exactly the old key.
    if bool(eddy) and _os_sb.environ.get("SB_EDDY_BE") != "1":
        _wmeta["ts"] = "bdf2"
    # Loaded ONLY on the path that can use it (coupled eddy, current drive) —
    # the mirror holds the whole A vector, and reading megabytes on a
    # magnetostatic or voltage run that will never consume them is work for
    # nothing.  `_warm_cache_load` returns the in-process slot first, so a
    # second run in the same process does not touch the disk at all.
    _wc_seed = None
    _wc_ok, _wc_why = False, "not a coupled-eddy current-drive run"
    if eddy and not _vdrive and not _warm_cache_disabled():
        _wc_seed = _warm_cache_load()
        _wc_ok, _wc_why = _warm_seed_accept(_wc_seed, _wmeta, I_phase_rms, rpm,
                                            gamma_deg, n_sectors=int(n_sectors))
        if not _wc_ok:
            _wc_seed = None
    _dm_seeded = False        # the Br ratchet continues a previous point's magnet
    _dm_seed_from = None      # …and this is whose (honesty fields in the result)
    # SWEEP MODE ONLY, and only on the path that publishes such a state: the
    # coupled-eddy current drive.  A magnetostatic or voltage run has a
    # different settling scheme (`_dmskip` / the voltage prefix) and never
    # publishes a Br map, so it must not consume one either.
    if (_seed_from_previous() and demag and _dmst is not None and eddy
            and not _vdrive and _wc_seed is not None
            and _wc_seed.get("br_val") is not None and _mag_idx.size
            and not _br_withheld_across_steps(_wc_seed, _wmeta)):
        try:
            _rm_seed = half["r"]["mesh"]
            _cen_r = np.asarray(_rm_seed.p, float)[:, np.asarray(_rm_seed.t, int)
                                                   ].mean(axis=1).T   # (n_tri, 2)
            _tag_r = np.zeros(int(_br_glob.size), int)
            for _tg, _ix in half["r"]["cells"].items():
                _tag_r[np.asarray(_ix, int)] = int(_tg)
            _proj = _project_br(_wc_seed, _cen_r[_mag_idx], _tag_r[_mag_idx])
            if _proj is not None:
                _kept, _nhit = _proj
                # `_br_glob` folds magnet_scale; the cache stores the pure
                # de-rating factor (1.0 = pristine), so a run at another scale
                # could not silently inherit this one's weakening — and the
                # scale is a meta term anyway.
                _br_glob[_mag_idx] = float(magnet_scale) * np.clip(_kept, 0.0, 1.0)
                _dm_seeded = True
                _dm_seed_from = {
                    "I": float(_wc_seed["I"]), "gam": float(_wc_seed["gam"]),
                    "rpm": float(_wc_seed["rpm"]),
                    "geometry_fingerprint": str(_wc_seed.get("geo_fp") or ""),
                    "same_geometry": bool(_wc_seed.get("geo_fp") == _geo_fp),
                }
                log.info(
                    "P2 demag seed: Br ratchet CONTINUED from the previous "
                    "point (%d of %d magnet elements matched by tag+centroid; "
                    "parent %.4g A / %.4g deg / geo %s; kept %.2f %% .. %.2f "
                    "%%).  The dedicated demag pre-pass is SKIPPED — the "
                    "ratchet is monotone and this magnet is the same magnet "
                    "one geometry step later (user 2026-09-06).",
                    _nhit, int(_mag_idx.size), float(_wc_seed["I"]),
                    float(_wc_seed["gam"]), str(_wc_seed.get("geo_fp") or "?"),
                    100.0 * float(np.min(_kept)), 100.0 * float(np.max(_kept)))
        except Exception as _dse:   # a bad seed may never fail a run
            log.info("P2 demag seed skipped (%s)", _dse)
            _dm_seeded = False; _dm_seed_from = None
    # Non-laminated solid conductors that ALSO carry rotating-field eddy losses
    # (in addition to magnets): the COILS (solid copper bars, stator side) and
    # the SHAFT (solid steel, rotor side).
    _coil_parts = [np.asarray(_i, int) for _t, _i in half["s"]["cells"].items()
                   if int(_t) >= DOM_COIL_BASE or int(_t) == int(DOM_COIL)]
    _coil_idx = np.concatenate(_coil_parts) if _coil_parts else np.array([], int)
    if "slot" in _ex_parts:
        # An EXCLUDED winding is air: no source (build_materials zeroed J_z),
        # no conductivity, and therefore no AC copper loss either.  The DC I²R
        # term is zeroed where it is assembled (P_cu_dc2 below) for the same
        # reason — reporting ohmic heat for a winding the field cannot see
        # would be the report contradicting the solve.
        _coil_idx = np.array([], int)

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
    # ── FRAME SCHEDULE AS A FUNCTION OF THE SETTLE COUNT ─────────────────
    # Built once here with the static defaults, and REBUILT after the dq
    # phasor initialiser has measured Ld/Lq at the operating point, when the
    # PWM settle is adapted to this machine's electrical time constant (see
    # SettlePolicy.adaptive_tau_mult below).  Everything the schedule derives —
    # frame count, settle prefix, coarse/fine split, Aitken boundaries,
    # progress composition — comes out of this one function, so the two builds
    # cannot disagree.  `dt` (the REPORTED window's step) does not depend on the
    # settle count in either scheme, which is what lets the eddy operators
    # and the drive object built between the two calls stay valid.
    #
    # The COUNT is the selected source policy (adapted below for PWM); the
    # schedule maths is the solver's and is unchanged.
    _n_periods0 = float(n_periods)

    def _build_schedule(_settle_p):
        n_periods = float(_n_periods0)
        n_total = max(1, int(round(n_steps_per_period * n_periods)))
        _v_settle_periods = 0        # imposed-current runs have no settle
        _vskip = 0
        _v_nspp = int(round(n_steps_per_period))
        if _vdrive:
            # Settling periods with ITERATED Aitken. The electrical time
            # constant L/R spans many periods on a low-R machine, so a marched DC
            # start-up decays too slowly to shed by brute force.  Instead: the
            # phasor init lands near the orbit, then the period-boundary flux
            # (which converges GEOMETRICALLY) is Δ²-extrapolated to its limit at
            # every 3rd boundary (anchors at periods 3, 6, 9 when reached;
            # each application
            # cuts the residual DC ~3×), and the final settling period runs after
            # the last anchor so the reported window starts on a clean orbit.
            # Settling frames use a REDUCED Picard depth (the DC dynamics only
            # need L roughly right); the last settling period + the reported
            # window run at full depth.
            _v_settle_periods = int(_settle_p)
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
        # WITH the coupled eddy solve, the demag settling MERGES into the eddy
        # warm-up instead of prepending its own period.  Both are settlings that
        # converge within about one electrical period (user's call, 2026-08-19:
        # one period suffices for both), the demag ratchet already runs on every
        # warm-up frame, and the warm-up's quiet test is extended below to refuse
        # the handoff while the Br map still moved — so the reported window starts
        # on a magnet AND an eddy field that have both stopped changing.  Measured
        # before the merge (12 steps): eddy+demag ×6.96 of plain; the second
        # settling period was pure duplication.
        if demag and _mag_idx.size and _v_nspp > 1 and n_total > 1 and not eddy:
            _dmskip = _v_nspp
            n_periods = float(n_periods) + 1.0
            n_total += _dmskip
        period_mech = 360.0 / pole_pairs                      # one electrical period [deg mech]
        dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total

        # ── FRAME SCHEDULE ───────────────────────────────────────────────────
        # The march used to be a uniform ramp, theta = (k/n_total)·period·n_periods
        # with one global dt.  It is now an explicit per-frame schedule so the
        # SETTLE can run coarse while the reported window runs fine (see
        # _PWM_COARSE_SETTLE).  The uniform branch below reproduces the old
        # expressions verbatim — same operations, same order — so every non-PWM run
        # is bit-identical to before.
        #
        # `_sched_th`  nominal rotor angle of the frame [mech deg]
        # `_sched_dth` nominal angular step of the frame [mech deg]
        # `_sched_dt`  nominal time step of the frame [s]
        # `_sched_t`   nominal elapsed time at the frame [s]
        # `_sched_fine` True on frames that run the REAL modulator
        _fine_frames = 0
        _c_nspp = _v_nspp
        _settle_bounds: set = set()      # frames that END a settle period (Aitken)
        # {last frame of a WHOLE settling period -> its first frame}, for the
        # DC-orbit solve.  Only periods of UNIFORM resolution go in: the flux
        # drift over a period measures the distance from ONE orbit only if
        # every frame of it rode that orbit.  (B5 / PWM study 2026-09-13.)
        _dc_win: dict = {}
        _sched_mixed = False
        # The source says whether the mixed scheme is ALLOWED at all
        # (SB_PWM_COARSE_SETTLE "0" turns it off, "1" forces it); the AUTO half
        # of the rule — mixed only while the run is itself a coarse pass, under
        # 16 steps per carrier — needs the run's own step count and so lives
        # here.  A source with no carrier can never take this path.
        _coarse_ok = bool(_settle.coarse_settle and _carriers and (
            _settle.coarse_settle_forced
            or float(_v_nspp) < 16.0 * max(1, _carriers)))
        # WHOLE fine settling periods at the end of the prefix (B5): at least
        # one coarse period has to survive or the "mixed" scheme is just the
        # all-fine march under another name.
        _fine_settle = 0
        if (_vdrive and _coarse_ok and (_vskip or _dmskip)):
            _c_nspp = _coarse_settle_nspp(_v_nspp)
            _ratio = _v_nspp // _c_nspp
            _settle_periods_total = int(_v_settle_periods) + (1 if _dmskip else 0)
            _fine_settle = min(max(1, int(_settle.fine_settle_periods)),
                               max(0, _settle_periods_total - 1))
            if _ratio > 1 and _fine_settle >= 1:
                _sched_mixed = True
        if _sched_mixed:
            _rep_frames = max(1, int(round(_v_nspp * (
                float(n_periods) - _settle_periods_total))))
            _dth_c = period_mech / _c_nspp
            _dth_f = period_mech / _v_nspp
            _dt_c = 1.0 / (max(f_elec, 1e-9) * _c_nspp)
            _dt_f = 1.0 / (max(f_elec, 1e-9) * _v_nspp)
            _sched_th, _sched_dth, _sched_dt, _sched_fine = [], [], [], []
            _th = 0.0
            # The settle used to end with a fine PRE-ROLL of a couple of
            # CARRIERS carved out of the last coarse period.  It cannot work:
            # the modulator turning on injects a DC of about half the ripple
            # amplitude, τ_e is ~27 electrical periods on this class of machine
            # (measured, L155), so nothing decays — and a window two carriers
            # wide is not a whole electrical period, so the DC in it cannot even
            # be MEASURED (every estimator has to compare against an
            # interpolated orbit, which is what made SB_PWM_HANDOVER_DC
            # over-correct).  Now the last `_fine_settle` settling periods are
            # marched WHOLE and fine, and the DC-orbit solve takes their DC
            # out on the flux drift, which is exact over a whole period.
            # (B5 / PWM study 2026-09-13; solve 2026-09-26.)
            _n_handover = 0
            for _pi in range(_settle_periods_total):
                _isf = (_pi >= _settle_periods_total - _fine_settle)
                if _isf and _pi == _settle_periods_total - _fine_settle and _pi:
                    # THE HANDOVER STEP ON THE FINE GRID (2026-09-26).  The
                    # last coarse frame sits at nP − Δθ_c; the fine periods
                    # end on nP − Δθ_f.  The first fine frame used to be put
                    # at nP, one COARSE-length step on, so the first fine
                    # "period" spanned P + Δθ_f − … — not a whole period:
                    # its flux drift carried the orbit's own motion over the
                    # extra step (measured, 30 mm fixture: 1.1e-4 Wb against
                    # 6e-6 from its DC) and the old DC anchor measured its
                    # DC over the same crooked window.  The r − 1 fine frames
                    # that complete the last coarse step close it: every
                    # solved period is whole, and every frame from nP on —
                    # the reported window's included — keeps its angle.
                    _th_h = _th - _dth_c
                    for _j in range(_ratio - 1):
                        _th_h += _dth_f
                        _sched_th.append(_th_h)
                        _sched_dth.append(_dth_f)
                        _sched_dt.append(_dt_f)
                        _sched_fine.append(True)
                        _n_handover += 1
                for _j in range(_v_nspp if _isf else _c_nspp):
                    _sched_th.append(_th)
                    _sched_dth.append(_dth_f if _isf else _dth_c)
                    _sched_dt.append(_dt_f if _isf else _dt_c)
                    _sched_fine.append(_isf)
                    _th += (_dth_f if _isf else _dth_c)
                # End of this settling period.  Recorded as the index of its
                # LAST frame, so the test in the loop is a membership check
                # instead of a modulo that no longer holds.  Every period here
                # is whole AND of uniform resolution, so both the DC-orbit
                # solve and the Δ² flux anchor may look at it — the latter only
                # where the source still asks for it (the sinusoid; it is off
                # on PWM since B5).
                if _settle.dc_orbit_solve:
                    _dc_win[len(_sched_th) - 1] = len(_sched_th) - (
                        _v_nspp if _isf else _c_nspp)
                if _aitken_on:
                    _settle_bounds.add(len(_sched_th) - 1)
            # fine frames in the prefix (the handover step's included)
            _fine_frames = _fine_settle * _v_nspp + _n_handover
            _skip_total = len(_sched_th)
            for _j in range(_rep_frames):
                _sched_th.append(_th); _sched_dth.append(_dth_f)
                _sched_dt.append(_dt_f); _sched_fine.append(True)
                _th += _dth_f
            # The settle prefix is now coarse, so the frame COUNT changed; every
            # consumer of n_total / _vskip / _dmskip downstream reads these.
            _vskip = _skip_total if _vdrive else 0
            _vskip_periods = _settle_periods_total
            _dmskip = 0                     # folded into the coarse prefix above
            n_total = len(_sched_th)
            dt = _dt_f                      # the REPORTED window's step
            _sched_t = [0.0] * n_total
            for _j in range(1, n_total):
                _sched_t[_j] = _sched_t[_j - 1] + _sched_dt[_j - 1]
            log.info("PWM mixed-resolution settling: %d x %d coarse (sinusoid "
                     "fundamental) + %d x %d fine settling + %d fine reported "
                     "= %d frames, against %d all-fine",
                     _settle_periods_total - _fine_settle, _c_nspp,
                     _fine_settle, _v_nspp, _rep_frames,
                     n_total, (_settle_periods_total + 1) * _v_nspp)
        else:
            _sched_th = [(k / n_total) * period_mech * n_periods
                         for k in range(n_total)]
            _dth_u = period_mech * n_periods / n_total
            _sched_dth = [_dth_u] * n_total
            _sched_dt = [dt] * n_total
            _sched_t = [k * dt for k in range(n_total)]
            _sched_fine = [True] * n_total
            _vskip_periods = float(_v_settle_periods) if _vdrive else 0.0
            # Δ² anchor points, on sources whose policy asks for them (they
            # anchor circuit STATE, which an imposed-current run does not have).
            if _aitken_on and _v_nspp > 0:
                _settle_bounds = {k for k in range(n_total)
                                  if (k + 1) % _v_nspp == 0 and (k + 1) < _vskip}
            # …and the whole settling periods the DC-orbit solve reads — every
            # discarded period, the demag settling one included (it is a whole
            # electrical period too, and the last of them runs free as the
            # solve's verification).
            if _settle.dc_orbit_solve and _v_nspp > 0:
                _dc_win = {k: k + 1 - _v_nspp for k in range(n_total)
                           if (k + 1) % _v_nspp == 0
                           and (k + 1) <= _vskip + _dmskip}
        # Composition string for the progress strip — the frontend cannot derive it
        # any more (it used to assume total = 3 x nspp), so the backend states it.
        _progress_comp = (
            ("%d×%d coarse settle + %d×%d fine settle + %d reported"
             % (int(_v_settle_periods) + (1 if _dmskip else 0) - _fine_settle,
                _c_nspp, _fine_settle, _v_nspp, n_total - _vskip - _dmskip))
            if _sched_mixed else
            ("%d/period × %d (%d settling + %d reported)"
             % (_v_nspp, int(round(n_total / max(_v_nspp, 1))),
                int(round((_vskip + _dmskip) / max(_v_nspp, 1))),
                int(round((n_total - _vskip - _dmskip) / max(_v_nspp, 1))))
             if (_vskip or _dmskip) and _v_nspp > 0
                and n_total % max(_v_nspp, 1) == 0
             else "%d frames" % n_total))
        return dict(
            _vskip=_vskip, _v_nspp=_v_nspp, _v_settle_periods=_v_settle_periods,
            n_periods=n_periods, n_total=n_total, _dmskip=_dmskip,
            period_mech=period_mech, dt=dt, _fine_frames=_fine_frames, _c_nspp=_c_nspp,
            _settle_bounds=_settle_bounds, _sched_mixed=_sched_mixed,
            _sched_th=_sched_th, _sched_dth=_sched_dth, _sched_dt=_sched_dt,
            _sched_t=_sched_t, _sched_fine=_sched_fine, _dc_win=_dc_win,
            _vskip_periods=_vskip_periods, _progress_comp=_progress_comp)

    _SCHED_KEYS = ('_vskip', '_v_nspp', '_v_settle_periods', 'n_periods',
                   'n_total', '_dmskip', 'period_mech', 'dt', '_fine_frames',
                   '_c_nspp', '_settle_bounds', '_sched_mixed', '_sched_th',
                   '_sched_dth', '_sched_dt', '_sched_t', '_sched_fine',
                   '_dc_win', '_vskip_periods', '_progress_comp')
    _S = _build_schedule(_settle_static_effective)
    (_vskip, _v_nspp, _v_settle_periods, n_periods, n_total, _dmskip,
     period_mech, dt, _fine_frames, _c_nspp, _settle_bounds, _sched_mixed,
     _sched_th, _sched_dth, _sched_dt, _sched_t, _sched_fine, _dc_win,
     _vskip_periods, _progress_comp) = [_S[_k] for _k in _SCHED_KEYS]

    _cancel_point("P2 sliding band: stitching and projections")
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
        SlipMortarDerivativeAction as _SlipMortarDerivativeAction,
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
        _pardiso2 = _own_pardiso(_pypard2.PyPardisoSolver())
    except Exception as _pae:
        log.info("pypardiso unavailable (%s) — using SuperLU for P2", _pae)
        _pardiso2 = None
    # A SECOND handle, mtype 2 (Cholesky), for the solves whose operator is
    # SPD by construction (P2Nonlinear.solve_ff(spd=True); proofs in
    # docs/CHOLESKY_SPD_2026-09-29.md).  Checked per matrix, LU otherwise.
    # SB_PARDISO_SPD=0 keeps every solve on the unsymmetric LU above.
    _pardiso2_spd = None
    if _pardiso2 is not None and _os_sb.environ.get("SB_PARDISO_SPD", "1") != "0":
        try:
            _h_spd = _pypard2.PyPardisoSolver(mtype=2)
            _need = ("_call_pardiso", "_check_b", "set_phase", "iparm")
            if not all(hasattr(_h_spd, _a) for _a in _need):
                raise RuntimeError("pypardiso %s lacks %s" % (
                    getattr(_pypard2, "__version__", "?"),
                    [_a for _a in _need if not hasattr(_h_spd, _a)]))
            _pardiso2_spd = _own_pardiso(_h_spd)
        except Exception as _pae:     # noqa: BLE001 — LU keeps working
            log.info("PARDISO Cholesky handle unavailable (%s) — LU only", _pae)
            _pardiso2_spd = None
    b2 = Basis(mesh_all, _P2E())
    b2_0 = b2.with_element(ElementTriP0())      # P0 for per-element ν interpolate
    N2 = b2.N
    _torque2 = _prepare_arkkio_torque_p2(
        mesh_all, b2, p.r_rotor_out, p.r_stator_in, p.stack_length)
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

    _cancel_point("P2 dof maps and sources")
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

    # ── SIX-PHASE WINDING: the same unit sources split by SET ────────────
    # Every coil tag belongs to one slot, every slot to one set
    # (``six_phase["slot_set"]``, from winding_sets).  f_set[s][ph] is the
    # unit-BRANCH-current source of set s's coils of phase ph, so
    # f_set[1] + f_set[2] == f_coil2 to round-off — measured and reported.
    _six = dict(six_phase) if isinstance(six_phase, dict) else None
    f_set2 = None
    _six_blk: Dict[str, Any] = {}
    if _six:
        _slot_set = list(_six.get("slot_set") or [])
        _pl = getattr(cs, "polys", polys)
        _clist = (_pl or {}).get("coils", []) if isinstance(_pl, dict) else []
        _nsl = len(dom.winding_layout)
        if len(_slot_set) != _nsl:
            raise ValueError(
                "six_phase.slot_set has %d entries for a %d-slot winding"
                % (len(_slot_set), _nsl))
        f_set2 = {s: {ph: np.zeros(N2) for ph in 'ABC'} for s in (1, 2)}
        for _ph in ('A', 'B', 'C'):
            _Iu = {'A': 0.0, 'B': 0.0, 'C': 0.0}; _Iu[_ph] = 1.0
            _mu = build_materials(_Iu, dom.winding_layout, _pl, 0.0,
                                  slot_area_m2, n_wires,
                                  coil_area_m2=_coil_area_meshed)
            for tag, idx in half["s"]["cells"].items():
                m_ = _mu.get(int(tag))
                if m_ is None or m_.J_z == 0.0:
                    continue
                _ci = int(tag) - DOM_COIL_BASE
                _si = (coil_slot_index(_clist[_ci], _nsl)
                       if 0 <= _ci < len(_clist) else None)
                if _si is None or _slot_set[_si] not in (1, 2):
                    raise ValueError("six-phase: coil tag %d has no set" % int(tag))
                sb = Basis(mesh_all, _P2E(), elements=np.asarray(idx, int))
                f_set2[_slot_set[_si]][_ph] += asm(_f1, sb) * m_.J_z
        _sum_err = max(
            float(np.max(np.abs(f_set2[1][ph] + f_set2[2][ph] - f_coil2[ph])))
            / max(float(np.max(np.abs(f_coil2[ph]))), 1e-300) for ph in 'ABC')
        _six_blk = {"split": _six.get("split"), "shift_deg": 0.0,
                    "phases": 6, "n_parallel": _six.get("n_parallel"),
                    "set1_paths": _six.get("set1_paths"),
                    "set2_paths": _six.get("set2_paths"),
                    "neutrals": _six.get("neutrals"),
                    "set_connection": _six.get("set_connection"),
                    "source_sum_residual": _sum_err,
                    "sets_in_model": sorted(
                        s for s in (1, 2)
                        if any(np.any(f_set2[s][ph]) for ph in 'ABC')),
                    "solve": ("the two sets are in phase and carry the "
                              "identical branch current: the source IS the "
                              "3-phase one (f_set1 + f_set2 = f_phase, "
                              "residual above)")}
        # The x-y probe drives the sets AGAINST each other.  A sector model
        # copies its own field into the other sectors, so it can only
        # represent that if the set pattern repeats with the sector — else
        # the probe needs the full ring (`measure_six_phase_inductances`).
        _secl = _nsl // max(int(NS), 1)
        _six_blk["xy_probe_valid"] = bool(
            _six_blk["sets_in_model"] == [1, 2]
            and all(_slot_set[k] == _slot_set[(k + _secl) % _nsl]
                    for k in range(_nsl)))

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
    # Per-frame torques (simulation/virtual_work_torque.frame_torques): the
    # Arkkio series as before, plus Coulomb virtual work on the rotor-side and
    # stator-side gap air rings (the slip circle bounds both, never inside).
    _tags_all2 = np.full(n_all_el, -1, int)
    for _hn, _off in (("s", 0), ("r", nst)):
        for tag, idx in half[_hn]["cells"].items():
            _tags_all2[np.asarray(idx, int) + _off] = int(tag)
    _ftq2 = _prepare_sb_frame_torques(
        mesh_all, _P2E(), stack_length_m=p.stack_length, sector_count=NS,
        maxwell_sector=_torque2, n_stator_nodes=nsn,
        air_mask=_vwt_air_mask(_tags_all2, _nu_const2),
        r_rotor_metal=_r_rot_max, r_slip=mid, r_stator_metal=_r_sta_min,
        slip_nodes_rotor=np.asarray(rring, int) + nsn,
        slip_nodes_stator=np.asarray(sring, int), log_warning=log.warning)

    # ── frame-independent solver helpers ─────────────────────────────────
    # The saturable-iron assembly, the Newton tangent and the damped-Picard
    # sweep — simulation/p2_nonlinear.py.  ONE object so the magnetostatic,
    # voltage-drive, eddy and phasor paths below cannot end up on different
    # nonlinearities; it owns the PARDISO handle for the same reason.
    # These used to be defined INSIDE the frame loop; they close over nothing
    # frame-specific (K_const2, _sat_sub2, _sat2, b2), and the voltage-drive
    # phasor initialiser below has to call them BEFORE the loop starts.
    _p2 = _P2Nonlinear(basis=b2, n_dof=N2, K_const=K_const2, sat=_sat2,
                       sat_sub=_sat_sub2, pardiso=_pardiso2, log=log,
                       pardiso_spd=_pardiso2_spd)

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
    # The derivative is an independent, diagnostic L2 mortar trace action.
    # Prepare its rotor-trace mass factorization once for the entire run.
    # OPT-IN (2026-09-24, held-item fix of a88ae64): the diagnostic costs a
    # pointwise stiffness re-assembly per frame and a trace factorization per
    # run, and no reported number reads it — so it runs only when
    # SB_P2_VIRTUAL_WORK=1 (cross-checks). Off, nothing is built or evaluated.
    _vw_enabled = _os_sb.environ.get("SB_P2_VIRTUAL_WORK", "0") == "1"
    _vw_action = None
    _vw_init_error = None
    if _vw_enabled:
        try:
            _vw_action = _SlipMortarDerivativeAction(
                _proj, math.radians(float(spacing)))
        except Exception as _vw_exc:  # noqa: BLE001 — diagnostic must not abort FEM
            _vw_init_error = type(_vw_exc).__name__
            log.warning("P2 virtual-work diagnostic unavailable: derivative "
                        "initialisation failed (%s: %s)", _vw_init_error, _vw_exc)
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
    #   • stator copper — ONE body per CONDUCTOR (tags ≥ DOM_COIL_BASE), each
    #     with its share of the phase current imposed exactly (∫J dΩ = I_b)
    #     while the eddy reaction redistributes J inside it.  I_b uses the
    #     SAME Iunit the P1 eddy path uses (read off _coil_con), so the two
    #     orders drive identical ampere-turns.  With ``wire_split`` = N a
    #     conductor is one STRIP (wire_width × wire_height) carrying the full
    #     branch current (the strips are series turns) — so the solved AC loss
    #     is the honest one for the narrow strip, not for the wide bar it
    #     replaced.
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
    def _bisected_magnet_pairs(mag_items):
        """[(tag on θ=0⁺, tag of the image piece)], info — the magnets the
        sector cut bisects (simulation/cut_bodies.py).  ``mag_items`` =
        [(tag, rotor-local element ids)].  A piece counts only when the mesh
        holds LESS than its CAD outline and it has a partner with the same
        cut-face radii on the other ray; failure → no pairs, said loudly."""
        if _full_ring or not mag_items:
            return [], {"pairs": 0}
        try:
            from motor_ai_sim.simulation.cut_bodies import (
                pair_bisected_bodies as _pair_cut,
                match_outlines as _match_ol2, meshed_area_m2 as _mA2)
            _rp_c = np.asarray(half["r"]["mesh"].p, float)
            _rt_c = np.asarray(half["r"]["mesh"].t, int)
            _loc_c = [np.asarray(_e, int) for _t, _e in mag_items]
            _ol_c = _match_ol2(
                _rp_c, _rt_c, _loc_c,
                [mp for mp, _pl in (polys.get("magnets") or [])],
                tags=[_t for _t, _e in mag_items], tag_base=int(DOM_MAG_BASE))
            _part_c = [(_g is None) or (_mA2(_rp_c, _rt_c, _e)
                                        < 0.99 * float(_g.area) * 1e-6)
                       for _g, _e in zip(_ol_c, _loc_c)]
            _pairs_c, _inf = _pair_cut(_rp_c, _rt_c, _loc_c, int(NS), 1e-6,
                                       is_partial=_part_c)
            _pt = [(int(mag_items[_i][0]), int(mag_items[_j][0]))
                   for _i, _j in _pairs_c]
            _inf = dict(_inf, pairs=len(_pt), pair_tags=_pt,
                        unpaired=[int(mag_items[_u][0])
                                  for _u in _inf.get("unpaired", [])])
            if _pt or _inf["unpaired"]:
                log.info("magnets bisected by the sector cut: %d pair(s) %s, "
                         "image sign %+d; unpaired cut-touching bodies %s "
                         "(own row)", len(_pt), _pt, int(_bc_sign),
                         _inf["unpaired"])
            return _pt, _inf
        except Exception as _e_cut:        # noqa: BLE001 — loud
            log.warning("bisected-magnet pairing failed (%s: %s) — every "
                        "magnet gets its own ∫J=0 row",
                        type(_e_cut).__name__, _e_cut)
            return [], {"pairs": 0, "error": str(_e_cut)}

    _ed_con = []             # constrained bodies: dicts(key, tag, g, S, …)
    _ed_paths = None         # series strand paths (strand_bonding="series")
    _cut_info = None         # magnets bisected by the sector cut (cut_bodies)
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
                    ("shaft" if int(_tg) == int(DOM_SHAFT)
                     else "sleeve" if int(_tg) == int(DOM_SLEEVE) else "mag",
                     int(_tg), np.asarray(_ix, int) + nst, _sg))
        _n_free_b = 0
        # ── MAGNETS BISECTED BY THE SECTOR CUT: one row per PHYSICAL magnet ──
        # (simulation/cut_bodies.py).  The two meshed pieces of a cut magnet —
        # its 0⁺ piece and the image of its other piece below θ = 2π/NS —
        # share ONE U (the image carrying s·U) and ONE net-current row with
        # g = g_0 + s·g_φ, S = S_0 + S_φ.  This replaces "U ≡ 0 on both halves",
        # which fixed U instead of the net current and was exact only for a
        # closed ring (2026-09-25; no catalogue machine has a cut magnet).
        # Every magnet that is NOT a paired piece — the interior ones, and a
        # piece whose partner cannot be found — carries its own ∫J = 0 row.
        _cut_pair_of: Dict[int, Tuple[int, int]] = {}
        _cut_info = {"pairs": 0}
        _mag_b = [(_tg, _ids) for _ky, _tg, _ids, _sg in _bodies if _ky == "mag"]
        if _mag_b and not _full_ring:
            _pairs_t, _cut_info = _bisected_magnet_pairs(
                [(_tg, np.asarray(_ids, int) - nst) for _tg, _ids in _mag_b])
            for _ta, _tb in _pairs_t:
                _cut_pair_of[int(_ta)] = (int(_tb), 0)
                _cut_pair_of[int(_tb)] = (int(_ta), 1)
        _n_old_half = sum(1 for _tg, _ids in _mag_b if _tg not in _int_mag)
        if _n_old_half and _n_old_half != 2 * len(
                [1 for _v in _cut_pair_of.values() if _v[1] == 0]):
            log.warning("P2 eddy: the area rule calls %d magnet(s) 'edge "
                        "halves' but the cut-geometry test paired %d piece(s) — "
                        "the geometry test decides", _n_old_half,
                        len(_cut_pair_of))
        _pair_gs: Dict[int, Tuple[Any, float]] = {}
        for _ky, _tg, _ids, _sg in _bodies:
            _Mb = (asm(_massform, Basis(mesh_all, _P2E(), elements=_ids))
                   * float(_sg)).tocsr()
            _Msig2 = _Msig2 + _Mb
            _Msig_grp[_ky] = (_Mb if _ky not in _Msig_grp
                              else _Msig_grp[_ky] + _Mb)
            if _ky == "mag" and int(_tg) in _cut_pair_of:
                _g_c = np.asarray(_Mb @ _ones_e).ravel()
                _pair_gs[int(_tg)] = (_g_c, float(_g_c.sum()))
                continue                 # its row is the pair's, below
            if _ky in ("shaft", "sleeve") and not _full_ring:
                # Cut by the anti-periodic boundary: the image half carries the
                # equal and opposite current, so the net axial current of the
                # modelled piece is identically zero and it needs no constraint
                # row.  Same argument the shaft has used since the sector solve
                # existed; the sleeve is the same kind of closed ring.
                _n_free_b += 1
                continue
            _g = np.asarray(_Mb @ _ones_e).ravel()
            _cm = _coil_meta.get(_tg) if _ky == "cu" else None
            _ed_con.append({
                "key": _ky, "tag": _tg, "tags": [_tg], "g": _g,
                "S": float(_g.sum()),
                "Iunit": float(_cm["Iunit"]) if _cm else 0.0,
                "phase": (_cm["phase"] if _cm else None),
                "slot": (int(_cm.get("slot", -1)) if _cm else -1),
                "coil": (int(_cm.get("coil", -1)) if _cm else -1),
            })
        # the bisected magnets' rows: one per physical magnet
        from motor_ai_sim.simulation.cut_bodies import (
            merge_pair_constraint as _merge_cut)
        for _t0, (_t1, _role) in sorted(_cut_pair_of.items()):
            if _role != 0:
                continue
            _g_m, _S_m = _merge_cut(_pair_gs[_t0][0], _pair_gs[_t0][1],
                                    _pair_gs[_t1][0], _pair_gs[_t1][1],
                                    int(_bc_sign))
            _ed_con.append({
                "key": "mag", "tag": _t0, "tags": [_t0, _t1],
                "signs": [1.0, (-1.0 if int(_bc_sign) < 0 else 1.0)],
                "g": _g_m, "S": _S_m, "Iunit": 0.0, "phase": None,
                "slot": -1, "coil": -1})

        # ── STRANDS IN HAND: one U per GROUP, not per strand ───────────────
        # Each conductor above carries its own ∫J = I row, which is the
        # PERFECTLY TRANSPOSED winding: every strand is forced to the same
        # current whatever flux it links.  A real k-in-hand coil is soldered at
        # its ends, so the strands are in PARALLEL — they share a voltage and
        # the flux-linkage difference between the rows drives a circulating
        # current between them (user 2026-09-11: "we'll solder the strand
        # ends together... circulating currents could arise there").
        #
        # Merging the k rows of a turn into ONE constraint is exactly that
        # parallel connection: the group gets a single U, each strand's current
        # comes out of its own ∫σ(U − ∂A/∂t), and the k currents differ by the
        # circulating term while summing to the turn current.
        #
        # WHICH k: the strand tags of one coil side run consecutively from the
        # slot bottom outwards (build_materials numbers them row by row), so a
        # turn is a run of `strand_group` consecutive tags inside one contiguous
        # block.  Untransposed, which is what a soldered-ends coil with no
        # transposition IS.
        #
        # BOUNDS, said plainly: this per-turn parallel is the UPPER bound on
        # the circulating loss (each turn free to circulate on its own), and
        # the per-strand rows are the LOWER bound (zero).  The real winding —
        # k paths in parallel over the WHOLE coil, series through its turns —
        # sits between them and needs a series-path circuit the bordered solver
        # does not have yet.
        _sg_k = int(strand_group or 1)
        if _sg_k > 1:
            _cu = [c for c in _ed_con if c["key"] == "cu"]
            _rest = [c for c in _ed_con if c["key"] != "cu"]
            _cu.sort(key=lambda c: c["tag"])
            _blocks, _run = [], []
            for _c in _cu:
                if _run and _c["tag"] != _run[-1]["tag"] + 1:
                    _blocks.append(_run); _run = []
                _run.append(_c)
            if _run:
                _blocks.append(_run)
            _merged, _n_bad = [], 0
            for _blk in _blocks:
                if len(_blk) % _sg_k:
                    # A block that does not divide is not a winding this rule
                    # describes; leave its strands independent and say so.
                    _n_bad += 1
                    _merged.extend(_blk)
                    continue
                for _i in range(0, len(_blk), _sg_k):
                    _grp = _blk[_i:_i + _sg_k]
                    if len({c["phase"] for c in _grp}) != 1:
                        _n_bad += 1
                        _merged.extend(_grp)
                        continue
                    _merged.append({
                        "key": "cu", "tag": _grp[0]["tag"],
                        "tags": [c["tag"] for c in _grp],
                        "g": np.sum([c["g"] for c in _grp], axis=0),
                        "S": float(sum(c["S"] for c in _grp)),
                        "Iunit": float(sum(c["Iunit"] for c in _grp)),
                        "phase": _grp[0]["phase"],
                    })
            _ed_con = _merged + _rest
            log.info("P2 eddy: strands in hand PARALLEL — %d conductor(s) "
                     "merged into %d group(s) of %d sharing one U%s",
                     len(_cu), sum(1 for c in _merged if len(c["tags"]) > 1),
                     _sg_k,
                     "" if not _n_bad else
                     " (%d block/group(s) left per-strand: not divisible by %d "
                     "or mixed phase)" % (_n_bad, _sg_k))

        # ── STRAND PATHS: the soldered-ends winding, solved exactly ───────
        # A coil of N turns wound with k wires in hand and joined ONLY at its
        # two ends is k conductors in parallel, each of which runs in SERIES
        # through all N turns.  Two things follow, and the per-strand rows get
        # the first one wrong while the per-turn merge gets the second one
        # wrong:
        #   * a strand carries the SAME current in every turn (the per-turn
        #     merge lets it differ from turn to turn — too much freedom, hence
        #     an upper bound);
        #   * the k currents need NOT be equal (the per-strand rows force them
        #     equal — no freedom at all, hence a lower bound).
        # Both are fixed by making the unknown a PATH current: one per strand
        # per coil, shared by all that strand's bodies, with the k paths of a
        # coil holding a common terminal voltage.  The bordered system grows by
        # one row per path plus one per coil (p2_drive.eddy_solve).
        #
        # WHICH BODIES: `slot` comes off the material name and
        # build_winding_layout puts a single-layer coil's two sides in slots
        # 2c / 2c+1, so `coil` groups the two sides.  Inside a side the tags
        # run in one radial sweep (verified against the meshed centroids: tag
        # 200…223 descend 89.8 → 75.6 mm, and 224…247 repeat it in the second
        # slot), so position m in the side is turn m//k, strand m%k — and the
        # same m sits at the same depth in BOTH sides, which is what an
        # untransposed coil wound round a tooth does.
        _ed_paths = None
        if series_paths:
            _k_sp = int(wire_parallel)
            _sides = {}
            for _bi, _c in enumerate(_ed_con):
                if _c["key"] != "cu" or int(_c.get("coil", -1)) < 0:
                    continue
                _sides.setdefault((int(_c["coil"]), int(_c["slot"])),
                                  []).append(_bi)
            _by_coil = {}
            for (_ci, _sl), _lst in _sides.items():
                _by_coil.setdefault(_ci, []).append((_sl, sorted(
                    _lst, key=lambda b: _ed_con[b]["tag"])))
            _paths, _pgrp, _prep, _bad = [], [], [], []
            _gi = -1
            for _ci in sorted(_by_coil):
                _ss = sorted(_by_coil[_ci])          # by slot index: 2c then 2c+1
                if len(_ss) != 2 or any(len(_x[1]) % _k_sp for _x in _ss) \
                        or len({len(_x[1]) for _x in _ss}) != 1:
                    _bad.append(_ci)
                    continue
                _gi += 1                             # one group per COIL
                # WHICH strand of side 2c+1 continues strand _sd of side 2c.
                # Same index is the coil wound as a flat ribbon that keeps its
                # face round the tooth — the bottom wire stays the bottom wire
                # — which is what a layer-wound concentrated coil does, and it
                # is the case that makes the two sides' flux differences ADD.
                # SB_STRAND_MIRROR=1 takes the other reading (the bundle flips)
                # so the answer's dependence on this can be measured instead of
                # assumed.
                _mir = _os_sb.environ.get("SB_STRAND_MIRROR") == "1"
                for _sd in range(_k_sp):
                    _mem = []
                    for _si, (_sl, _lst) in enumerate(_ss):
                        _s0 = (_k_sp - 1 - _sd) if (_mir and _si) else _sd
                        _mem += [(_lst[_m], (1.0 if _ed_con[_lst[_m]]["Iunit"]
                                             >= 0.0 else -1.0))
                                 for _m in range(_s0, len(_lst), _k_sp)]
                    _prep.append(next(b for b, sg in _mem if sg > 0.0))
                    _paths.append(_mem)
                    _pgrp.append(_gi)
            if _bad:
                raise RuntimeError(
                    "strand_bonding='series': coil(s) %s do not resolve into "
                    "two equal sides divisible by %d wires in hand — the coil "
                    "map is not what this winding is, and guessing it would "
                    "silently solve a different machine" % (_bad, _k_sp))
            if _paths:
                _ed_paths = {"paths": _paths, "group": _pgrp, "rep": _prep,
                             "n_group": (max(_pgrp) + 1)}
                log.info("P2 eddy: strands in hand SOLDERED AT THE ENDS — "
                         "%d conductor(s) resolved into %d strand path(s) of "
                         "%d bodies over %d coil(s), %d in hand; one current "
                         "per PATH, the %d paths of a coil sharing its "
                         "terminal voltage",
                         sum(len(p) for p in _paths), len(_paths),
                         len(_paths[0]), _ed_paths["n_group"], _k_sp, _k_sp)

        _gcols = [c["g"] for c in _ed_con]
        if _gcols:
            from scipy.sparse import csc_matrix as _csc2, hstack as _hstack2
            # CSC columns avoid a dense dof-by-body temporary and keep each
            # column's index pointer small; the assembled operator stays CSR.
            _G2 = _hstack2([_csc2(g.reshape(-1, 1)) for g in _gcols],
                          format="csr")
            _Sdt2 = np.array([c["S"] for c in _ed_con], float) * dt
        _Msd2 = (_Msig2 * (1.0 / dt)).tocsr()
        # per-constraint S_b = ∫σ dΩ (the midpoint loss sampling reads it)
        _S_con = np.array([c["S"] for c in _ed_con], float)
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
        # corners and edges facing the changing field, which is what a commercial FEM
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
        # A merged group owns several tags: its U paints every element of all
        # of them (the strands share one U by construction).
        # NB `_t` is this module's alias for `time` — never a loop variable here.
        _ed_uloc = [np.concatenate([_ed_loc_by_tag[int(_tg_i)]
                                    for _tg_i in _c.get("tags", [_c["tag"]])])
                    for _c in _ed_con]
        # …and the sign U takes there: +1, except on the image piece of a
        # magnet bisected by the sector cut, which carries s·U (cut_bodies).
        _ed_usgn = [np.concatenate([
            np.full(_ed_loc_by_tag[int(_tg_i)].size, float(_sg_i))
            for _tg_i, _sg_i in zip(_c.get("tags", [_c["tag"]]),
                                    _c.get("signs", [1.0] * len(
                                        _c.get("tags", [_c["tag"]]))))])
                    for _c in _ed_con]
        _ed_gmask = {_k: (_ed_key_e == _k)
                     for _k in ("cu", "mag", "shaft", "sleeve")}

    _cancel_point("P2 flux linkage and circuit")
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
        if not eddy:
            # The imposed-current RHS uses uniform J over each slot's meshed
            # copper.  Individual conductor tags can have slightly different
            # mesh areas, so summing their *unweighted* means is not the exact
            # adjoint of that RHS.  Use the same assembled unit-current source
            # for terminal linkage and winding work in the magnetostatic path.
            return tuple(_sc_psi2 * float(A2 @ f_coil2[ph]) for ph in 'ABC')
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

    # The zero-sequence inductance a DELTA loop sees is measured AFTER the
    # transient instead of here — see the STAR / DELTA block.  It has to be:
    # on the I = 0 linear iron state available at this point the probe reads
    # ~4.5x the bench Ld (the magnets have already pushed the teeth up the BH
    # curve, and the small-signal inductance at the operating point is what a
    # circulating current actually meets).  Only the RATIO L0/Ld survives that
    # state, and the absolute value is what the loss needs.
    _iv_state = {'A': 0.0, 'B': 0.0, 'C': 0.0}
    _psi_prev = None
    _th_eff_prev = None      # previous frame's SNAPPED rotor angle (rotor time)
    _dt_k = dt
    # ── WHAT THE SOURCE IS TOLD ABOUT THE MACHINE ────────────────────────
    # The last CONVERGED step's terminal currents and flux linkages, handed to
    # the source in its Feedback.  Kept SEPARATE from _iv_state / _psi_prev,
    # which the Aitken anchor and the DC-orbit solve deliberately move off
    # the solved values: a controller must see what the machine DID, not the
    # solver's settling bookkeeping.  None on the first frame — there is no
    # previous step, and a source that closes a loop must handle that.
    _fb_i_prev = None
    _fb_psi_prev = None
    _fb_v_bus = getattr(_src, "v_bus", None)
    _fb_v_bus = float(_fb_v_bus) if _fb_v_bus else None
    _v_diag = {"iters": [], "resid": []}
    _v_bpsi = []             # period-boundary flux samples for the Aitken anchor
    # How often the Δ²-anchor was TRIED vs actually APPLIED.  Reported, because
    # the ablation (see drive.aitken_flux_anchor) found that on this machine the
    # guards skip every single attempt — the anchor is a no-op here, and that was
    # only discoverable by removing it and comparing.  Now it is a number.
    _v_anchor_tries = 0
    _v_anchor_applied = 0
    # …and the DC-orbit solve (simulation/dc_orbit.py), built on the final
    # schedule after the phasor initialiser.  None = this run does not solve
    # for it (imposed current, the sinusoid's Δ² anchor, no whole settling
    # period).
    _dc_orbit = None
    _dc_starts: dict = {}    # first frame of a solved period -> its last frame
    _dc_verify_k = None      # last frame of the free (verification) period

    # The voltage-drive, eddy and coupled eddy+voltage Newtons —
    # simulation/p2_drive.py.  ONE object rather than three closures: the
    # third solver exists precisely because the first two must not be allowed
    # to answer for each other, and that is a property of the code layout as
    # much as of the physics.  R_phase, psi and the coil source columns are
    # bound here because they are run-dependent; Pro/free stay per-call.
    # v_phase_peak is only a SCALE for the circuit residual norm; take it from
    # the source so a hand-written one is scaled by the voltage it actually
    # applies rather than by an argument it never used.
    _drv = _P2Drive(
        p2=_p2, psi=_psi2, f_mag=f_mag2, Pa=_Pa2, Pb=_Pb2, R_phase=R_phase,
        v_phase_peak=float(getattr(_src, "v_phase_peak", None) or v_phase_peak),
        n_dof=N2, pic_tol=_PIC_TOL2, dt=dt, log=log,
        ed_con=(_ed_con if eddy else None), G=_G2, Msig=_Msig2, Msd=_Msd2,
        Sdt=_Sdt2, paths=(_ed_paths if eddy else None))

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
        _V0 = _src.fundamental(0.0)
        # θ_eff = 0 electrical.  The absolute offset CANCELS: _align below
        # measures the PM-flux angle relative to it, so _thal is the true
        # PM-flux angle in the ABC frame whatever reference is used here.
        _the0 = _m.radians(0.0 * pole_pairs + daxis_eff)

        _park = _park_dq; _ipark = _ipark_dq   # simulation/drive.py
        _id0 = _iq0 = 0.0; _thal = _the0
        _psi_pm_d = 0.0; _Ldd = _Lqq = _Ldq = _Lqd = 1e-6
        _Aop = np.zeros(N2)
        for _it in range(max(int(nonlinear_iterations), 20)):
            # one factorisation + solve per sweep — a Stop may land in any
            _cancel_point("phasor initialiser, sweep %d" % _it)
            _Kph = _p2.asmK(_nu_ph)
            _Kff0 = (_Pro0v.T @ _Kph @ _Pro0v).tocsr()[_free0v][:, _free0v].tocsc()
            _X0 = _p2.solve_ff(_Kff0, _RHS0, spd=True)
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
        # ψ at t = −Δt₀ on the orbit: dq are constant in steady state, so the
        # previous-step flux is the SAME dq vector mapped one FRAME back —
        # the park angle rotated by ω·Δt₀ (NOT one slip node; a frame spans
        # many).  Getting this wrong injects a spurious rotational EMF at
        # frame 0 → a decaying DC current.
        # Δt₀ is the FIRST MARCHED FRAME's step, not the reported window's.
        # They are the same number on every uniform schedule, and differ by the
        # coarse:fine ratio on the mixed PWM one — where using the fine dt put
        # ψ_prev |ψ|·ω·(ratio−1)·dt_f off the orbit and, over the operating-
        # point inductance, started the march with a 42 A DC that τ_e ≈ 27
        # electrical periods then could not shed (measured, L155 72-step
        # diagnostic: 41.5 A at ratio 2).  Set after the settle adaptation
        # below, because that can rebuild the schedule.  (B5 / PWM study
        # 2026-09-13.)
        _psd = _psi_pm_d + _Ldd * _id0 + _Ldq * _iq0
        _psq = _Lqd * _id0 + _Lqq * _iq0
        log.info("P2 vdrive phasor init: Ld=%.4g Lq=%.4g H |psi_pm|=%.4g Wb "
                 "i_dq=(%.1f, %.1f) A i0=(%.1f, %.1f, %.1f)",
                 _Ldd, _Lqq, _psi_pm_d, _id0, _iq0, _iA0, _iB0, _iC0)
        # THE CONTROLLER'S CURRENT LOOP (owner 2026-09-29): a closed-loop
        # bridge source is tuned on the inductances just measured here.
        if hasattr(_src, "configure_current_loop"):
            _src.configure_current_loop(R_phase=float(R_phase),
                                        L_d=float(_Ldd), L_q=float(_Lqq))
        # ── SETTLE ADAPTED TO THIS MACHINE'S L/R (user 2026-09-02) ──────
        # The PWM settle used to be a flat 2 periods, validated on a machine
        # whose L/R was ~0.75 electrical period.  On CILN28/G2-L40 (L/R = 4.8
        # periods at 3000 rpm) that left a 9 A DC in the phase currents after
        # the settle — a 10.7 N·m first-order torque pulsation reported as
        # 44 % "ripple" (machine's own: 6 %).  Now: periods = ceil(k·τ_e/T_e)
        # with τ_e = max(Ld, Lq)/R from the phasor initialiser above, never
        # below the static default, capped.  ≥3 periods also switches the
        # Aitken Δ² anchors on (they need three period boundaries).  Which
        # sources adapt is theirs to say (SettlePolicy.adaptive_tau_mult): the
        # PWM source does, the sinusoidal voltage drive does NOT — it keeps its
        # pinned 10-period march (its Aitken anchoring already handles long τ,
        # and every regression number was produced with it) — and an EXPLICIT
        # SB_V_SETTLE_PERIODS turns the rule off for everyone.
        if _settle.adaptive_tau_mult is not None and R_phase > 0:
            _tau_mult = float(_settle.adaptive_tau_mult)
            _tau_e = max(float(_Ldd), float(_Lqq)) / float(R_phase)
            _T_e = 1.0 / max(float(f_elec), 1e-9)
            _need = int(_m.ceil(_tau_mult * _tau_e / _T_e))
            _sp_pwm = int(min(_settle.periods_cap,
                              max(_settle.periods_static, _need)))
            if _sp_pwm != int(_v_settle_periods):
                log.info("P2 vdrive settle adapted to L/R: tau_e = %.3g ms = %.2f "
                         "electrical periods -> %d settle periods (was %d; k = %.1f, "
                         "cap %d)", _tau_e * 1e3, _tau_e / _T_e, _sp_pwm,
                         int(_v_settle_periods), _tau_mult,
                         int(_settle.periods_cap))
                _S = _build_schedule(_sp_pwm)
                (_vskip, _v_nspp, _v_settle_periods, n_periods, n_total, _dmskip,
                 period_mech, dt, _fine_frames, _c_nspp, _settle_bounds, _sched_mixed,
                 _sched_th, _sched_dth, _sched_dt, _sched_t, _sched_fine, _dc_win,
                 _vskip_periods, _progress_comp) = [_S[_k] for _k in _SCHED_KEYS]
                _dt_k = dt
        # ψ(−Δt₀) — see the phasor-init comment above.  `_sched_dt[0] == dt`
        # bit-for-bit on every uniform schedule, so nothing outside the mixed
        # PWM one moves.
        _psi_prev = dict(zip(('A', 'B', 'C'),
                             _ipark(_psd, _psq, _thal - _w * float(_sched_dt[0]))))
        # ── THE DC-ORBIT SOLVE (simulation/dc_orbit.py, 2026-09-26) ─────
        # On the FINAL schedule (the adaptive block above may have rebuilt
        # it): every whole settling period is solved on, the last runs free.
        if _settle.dc_orbit_solve and _dc_win:
            _dc_starts = {int(_k0): int(_k1) for _k1, _k0 in _dc_win.items()}
            _dc_verify_k = max(_dc_win)
            _dc_orbit = _DcOrbitSolve(R_phase=float(R_phase),
                                      correcting=bool(_settle.dc_orbit_correct))
            log.info("P2 vdrive DC-orbit solve: %d whole settling period(s), "
                     "%s", len(_dc_win),
                     ("%d corrected + 1 free (verification)" % (len(_dc_win) - 1)
                      if _dc_orbit.correcting else
                      "MEASURED ONLY — corrections off (SB_V_DC_SOLVE=0)"))

    # ── frame loop ───────────────────────────────────────────────────────
    _T2 = []; _T_vw = []; _T_vw_reason = []; _Tc2 = []; _Tc2l = []
    _psiA = []; _psiB = []; _psiC = []; _tt = []; _theta_samples = []
    _dt_steps = []          # Δt each reported frame's step was solved with
    _pre_frame = None       # (ψ, i) of the frame solved just before frame 0
    _IA = []; _IB = []; _IC = []
    # APPLIED terminal voltage per solved step (imposed-voltage sources only) —
    # the excitation chart's series.  Empty on the imposed-current sources,
    # where what was applied IS the current already in _IA/_IB/_IC.
    _vapp = {'A': [], 'B': [], 'C': []}
    # ── incremental (frozen-permeability) d-q inductances ────────────────
    # WHICH reported frames are probed, decided before the loop so the choice
    # cannot depend on anything the loop discovers.  Evenly spaced over the
    # whole reported window; `_INC_LDQ_SAMPLES` says why four.
    _inc_rows: list = []
    _six_rows: list = []    # six-phase L_xy / L_dq,set probe rows
    _six_psi: list = []     # (theta_e, psi_set1_A, psi_set2_A) per frame
    _inc_at: set = set()
    # (A converged settle does not know its reported window yet: it picks the
    # probed frames inside that window when the settle stops — see the loop.)
    if inc_ldq and n_total > 0 and _conv_settle is None:
        _ns_inc = max(1, min(int(_INC_LDQ_SAMPLES), int(n_total)))
        _inc_at = {int(round(_j * n_total / _ns_inc)) % int(n_total)
                   for _j in range(_ns_inc)}
    _pic_iters = []; _pic_res_max = 0.0; _frame_converged = []
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
    # AIR GAP: one area-weighted mean |B| per frame (see the element set above).
    _gap_idx2 = (np.concatenate([_gap_s_idx, _gap_r_idx + nst])
                 if (_gap_s_idx.size or _gap_r_idx.size) else np.array([], int))
    _bgap2: list = []
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
    # Window-mean element ν of the ROTOR over the reported frames: the frozen
    # permeability the frequency-domain rotor route linearises on (2026-09-25;
    # it used μ_r = 1 in the shaft and ONE mean μ_r for the back iron).
    _nu_rot_sum = None; _nu_rot_n = 0
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
    # The extension is whole periods, "as many as it takes" (2026-09-24): a
    # march must END at θ = −dθ to hand frame 0 a field exactly one dt old, so
    # each extension splices one more whole period in front and carries the
    # rotor eddy state across it with the exact pole-pair map (below), which
    # keeps the march continuous in time however many periods it takes — up
    # to SB_EDDY_MAX_PERIODS (16), after which the run says it is capped.
    # n_warmup and the measured residual travel out in the result dict.
    _EDDY_SETTLE_TOL = 0.02
    # copper I²R for the settle gauge's machine total (minor-body rule)
    try:
        _warm_cu_W = max(0.0, float(P_cu))
    except Exception:
        _warm_cu_W = 0.0
    _eddy_probe = 2 if (eddy and not _vdrive) else 0
    _eddy_cap = int(n_steps_per_period) if (eddy and not _vdrive) else 0
    # Benchmarks / tests: pin the warm-up to a fixed count and take whatever
    # it gives (SB_EDDY_WARM=2 is the pre-adaptive behaviour, =0 the raw cold
    # start).  The residual is still measured and still reported.
    _ew_env = _os_sb.environ.get("SB_EDDY_WARM")
    if _ew_env not in (None, "") and eddy and not _vdrive:
        _eddy_probe = max(0, int(_ew_env)); _eddy_cap = 0
    _warm_solid: List[float] = []   # solid σE² per frame of the CURRENT march
    _warm_ks: List[int] = []        # frame index of each sample (for angles)
    # ── HONEST SETTLING (no-filter pass, 2026-09-24) ─────────────────────────
    # Measured on the owner's duties: the 3-sample probe above read "settled"
    # on L13 / Ø40 (0.7-1.3 %) while the shaft loss of the same run, marched 5
    # continuous periods, came out HALF (L13 without demag 0.83 -> 0.41 W; L155
    # rated 29.1 -> 15.0 W with the old one-period extension).  Two defects:
    #  (1) the gauge: three samples, or block means over half a period of the
    #      magnet+shaft SUM, cannot see a slow body (the shaft ring, τ ≈ 1-3 ms)
    #      behind a fast dominant one;
    #  (2) the splices: every extension / pre-pass jumped the rotor back one
    #      electrical period while keeping the per-dof eddy history, which is
    #      the history of a DIFFERENT rotor position — a fresh kick each time.
    # The cure, not a filter: every splice carries the rotor-dof state across
    # the period with the exact pole-pair map (rotor_window.period_shift_map:
    # on the periodic steady state the state at θ−Θe IS the image of the state
    # at θ), so the march is CONTINUOUS in time; and the verdict is taken on
    # WHOLE-PERIOD means per conductor group (sb_postproc.eddy_period_resid),
    # extending period by period until each body is quiet or the cap is hit —
    # a capped run says so (eddy_settled False).
    _EDDY_MAX_WARM_PERIODS = max(1, int(
        _os_sb.environ.get("SB_EDDY_MAX_PERIODS", "16") or 16))
    # CAP 24 FOR SLOW BODIES (owner 2026-09-27).  A body whose slow mode, as
    # the periodic accelerator MEASURES it on the state (λ per period, τ =
    # −1/ln λ periods), decays slower than the normal cap is long may march
    # up to SB_EDDY_MAX_PERIODS_SLOW (24) periods.  Criterion: τ > the normal
    # cap (L155 shaft λ 0.942 → τ 16.7 > 16).  A fast machine never gets a λ
    # from the accelerator, keeps the normal cap and is bit-identical.  An
    # explicit SB_EDDY_MAX_PERIODS pin is a pin: never raised.
    _EDDY_MAX_WARM_PERIODS_SLOW = max(_EDDY_MAX_WARM_PERIODS, int(
        _os_sb.environ.get("SB_EDDY_MAX_PERIODS_SLOW", "24") or 24))
    _eddy_cap_pinned = bool(_os_sb.environ.get("SB_EDDY_MAX_PERIODS"))
    _eddy_cap_periods = _EDDY_MAX_WARM_PERIODS      # the cap in force
    _eddy_slow_cap: Dict[str, Any] = {}             # why it was raised
    _acc_lam_state: List[float] = []                # λ read at slow-mode checks
    _warm_grp: Dict[str, List[float]] = {}   # per conductor group, continuous
    _warm_ext_periods = 0           # whole periods spliced in (extensions)
    _warm_gauge: Dict[str, Any] = {}          # the verdict's own numbers
    _shift_cache: Dict[str, Any] = {}
    # ── PERIODIC-STATE ACCELERATOR (2026-09-26, periodic_accel.py) ──────────
    # A slow rotor conductor (L155 shaft wall, τ ≈ 17-18 periods via μ(B))
    # cannot settle inside the cap by marching.  At the end of every extension
    # period the rotor-conductor state (both BDF2 levels, carried across the
    # period by the exact pole-pair map) is one iterate of the period map; RRE
    # over `_ACC_K` + 2 of them jumps to the linearised fixed point.  Only the
    # DISCARDED prefix moves: the gauge then needs MIN_VERIFY_PERIODS whole
    # continuous periods solved after the last jump (its record is restarted
    # at the jump), unchanged otherwise.  A machine that settles before
    # `_ACC_SKIP` + `_ACC_K` + 2 periods never collects enough states, so the
    # accelerator is a no-op there.  SB_EDDY_ACCEL=0 switches it off.
    _acc_on = bool(eddy and not _vdrive
                   and _os_sb.environ.get("SB_EDDY_ACCEL", "1") != "0")
    _ACC_SKIP = max(0, int(_os_sb.environ.get("SB_EDDY_ACCEL_SKIP", "1") or 1))
    _ACC_K = max(1, int(_os_sb.environ.get("SB_EDDY_ACCEL_K", "2") or 2))
    # after a jump: periods left to the fast modes the jump re-excites before
    # the next cycle collects, and that cycle's (smaller) order
    _ACC_POST_SKIP = max(0, int(_os_sb.environ.get("SB_EDDY_ACCEL_POST_SKIP", "1")
                                or 1))
    _ACC_K2 = max(1, int(_os_sb.environ.get("SB_EDDY_ACCEL_K2", "1") or 1))
    _acc_skip_left = _ACC_SKIP            # decisions still to skip this cycle
    _acc_k_now = _ACC_K                   # RRE order of the current cycle
    _acc_hist: List[np.ndarray] = []      # last 4 period states (slow check)
    _acc_last: Dict[str, Any] = {}        # slow bodies/map of the last jump
    _acc_lams: List[float] = []           # lambda measured by single-mode jumps
    _acc_dec: List[int] = []              # decision number of each state
    _acc_sec: Dict[str, Any] = {}         # single-mode jump -> secant memo
    _acc_states: List[np.ndarray] = []    # period-map iterates since last jump
    _acc_idx = None                       # rotor-conductor dofs (σ > 0)
    _acc_w = None                         # lumped σ-mass (the residual norm)
    _acc_gmask: Dict[str, np.ndarray] = {}  # group → mask over _acc_idx
    _acc_jumps: List[Dict[str, Any]] = []  # what each jump did (in the result)
    _acc_seen = 0                         # extension decisions seen
    # ── DC ERROR CORRECTION of slow solid rings (2026-09-29) ────────────────
    # docs/EDDY_SHAFT_SETTLE_2026-09-29.md.  After the extension periods in
    # SB_EDDY_EEC_AT (default "2,4") the rotor-frame DC error of a slow ring
    # conductor (the L155 shaft wall: DC diffusion, τ ≈ 21 periods) is
    # corrected by ONE static solve of the Jacobian (unclamped tangent)
    # averaged over the period just marched
    # (periodic_accel.dc_error_correction, TP-EEC), then
    # verified like any accelerator jump: an ACCELERATOR of the discarded
    # prefix, not an exact step.  It replaces the RRE accelerator;
    # SB_EDDY_EEC=0 restores it.
    _eec_on = bool(eddy and not _vdrive and not _full_ring
                   and _os_sb.environ.get("SB_EDDY_EEC", "1") != "0")
    # bodies it may correct: the closed rings cut by the (anti-)periodic
    # boundary (no ∫J row, U ≡ 0 by symmetry) — shaft and sleeve
    _eec_groups = [_gk for _gk in ("shaft", "sleeve") if _gk in _Msig_grp]
    _eec_at = sorted({int(_s) for _s in (_os_sb.environ.get(
        "SB_EDDY_EEC_AT", "2,4") or "").split(",") if _s.strip()})
    _eec_J = None                 # Σ over the period's frames of K + T (unclamped)
    _eec_nJ = 0
    _eec_x0 = None                # the period's start state (same labelling)
    _eec_lam: List[float] = []    # λ of the slow mode seen by each correction
    _eec_accum = False            # average J over the period being marched
    _eec_sig: Dict[str, str] = {}  # per ring body: why it may / may not be corrected
    _eec_dhdb = None              # min (dH/dB)/ν over the averaged frames
    _eec_last: Dict[str, Any] = {}  # bodies / map / σ-mass of the last correction
    if _eec_on:
        _acc_on = False

    def _period_shift(vec):
        """Rotor part of a per-dof state carried one electrical period BACK
        (θ → θ − Θe), exactly on the periodic steady state; the stator part is
        stator-frame periodic and stays.  Identity (with a warning) if the
        rotor dofs are not pole-pair periodic."""
        if "map" not in _shift_cache:
            try:
                _edf = np.asarray(b2.element_dofs)
                _rdf = np.unique(_edf[:, nst:].ravel())
                _sdf = np.unique(_edf[:, :nst].ravel())
                if np.intersect1d(_rdf, _sdf).size:
                    raise RuntimeError("stator and rotor halves share dofs")
                _sgn_th = (1.0 if (float(period_mech) * float(n_periods)
                                   / max(float(n_total), 1.0)) >= 0.0 else -1.0)
                _mp, _mi = _period_shift_map(
                    np.asarray(b2.doflocs, float)[:, _rdf],
                    float(np.sqrt(np.min(_triangle_areas(half["r"]["mesh"])))),
                    n_sectors=int(NS), bc_sign=int(_bc_sign),
                    rot_rad=-_sgn_th * math.radians(float(period_mech)))
                _shift_cache["map"] = (None if _mp is None
                                       else (_rdf, _mp[0], _mp[1]))
                _shift_cache["info"] = _mi
            except Exception as _e_sh:        # noqa: BLE001 — identity, loudly
                _shift_cache["map"] = None
                _shift_cache["info"] = {"error": "%s: %s"
                                        % (type(_e_sh).__name__, _e_sh)}
            if _shift_cache["map"] is None:
                log.warning("P2 eddy warm-up: rotor dofs are not pole-pair "
                            "periodic (%s) — splices keep the raw per-dof "
                            "history (a kick per splice)", _shift_cache["info"])
        _m = _shift_cache["map"]
        if _m is None:
            return np.array(vec, float, copy=True)
        _rdf, _jj, _ss = _m
        _out = np.array(vec, float, copy=True)
        _out[_rdf] = _ss * np.asarray(vec, float)[_rdf[_jj]]
        return _out

    def _rotor_image_mean(vec):
        """``vec`` with the dofs of its solid RING conductors (shaft, sleeve)
        replaced by their mean over the pole-pair images
        (periodic_accel.pole_pair_image_mean), and what was removed.

        Why (2026-09-29, docs/EDDY_SHAFT_SETTLE_2026-09-29.md): on the
        periodic orbit the ROTOR-FRAME DC field of a conductor — its mean over
        the q periods the image map needs to close — is invariant under the
        pole-pair image map S; the non-invariant content of the orbit is AC in
        the rotor frame and lives in the conductor's skin layer.  A one-angle
        static snapshot instead freezes the INSTANTANEOUS non-invariant field
        (slotting and MMF harmonics at that angle) into the whole conductor.
        In a solid magnetic shaft that imprint is a set of rotor-fixed DC
        patterns (the S-eigen-sectors m ≠ 0) that decay only by DC diffusion
        through the wall — measured on the L155: |λ| = 0.963-0.967 per period
        (τ ≈ 28 periods), turning exactly 2π/5 per period under the
        relabelling, and 15-50× the pole-pair-periodic change in the σ-norm.
        Its loss is a 1.2·f_e line that no whole-period mean cancels: the
        5-period beat that kept the settle gauge at 15-108 % until the cap.
        Starting from the image mean removes that imprint and nothing the
        orbit's slow part has (the invariant sector is untouched).  Magnets are
        left as they are: they settle inside a period (τ ≈ 0.1 period on the
        L155) and averaging their history only kicked them (Ø40: one more
        warm-up period, measured)."""
        _cm = None
        for _gk in ("shaft", "sleeve"):
            if _gk in _Msig_grp:
                _dgk = np.asarray(_Msig_grp[_gk].diagonal()).ravel() > 0.0
                _cm = _dgk if _cm is None else (_cm | _dgk)
        if _cm is not None and "mag" in _Msig_grp:
            # dofs shared with a magnet keep the static value: averaging them
            # would move the magnet's own history at its face (measured: a
            # 4.9 kW one-frame kick in the L155 magnets)
            _cm = _cm & ~(np.asarray(_Msig_grp["mag"].diagonal()).ravel() > 0.0)
        _period_shift(np.zeros(N2))              # builds the map
        _m = _shift_cache.get("map")
        _n_ring = 0 if _cm is None else int(np.count_nonzero(_cm))
        _why = _acc_img_refusal(_full_ring, 1.0, _n_ring)
        if _why is None and _m is None:
            _why = "no exact pole-pair map"
        if _why is None:
            _ci = np.flatnonzero(_cm)
            _pm, _sg = _acc_restrict(_m[0], _m[1], _m[2], _ci)
            _q, _sig, _reg = _acc_cycles(_pm, _sg)
            _why = _acc_img_refusal(_full_ring, _sig, _n_ring)
        if _why is not None:
            log.info("P2 eddy warm-up: one-angle static start kept (%s)", _why)
            return np.array(vec, float, copy=True), {"refused": _why}
        _x = np.asarray(vec, float)[_ci]
        _xm = _acc_image_mean(_x, _pm, _sg, _q, _sig)
        _out = np.array(vec, float, copy=True)
        _out[_ci] = _xm
        _dgs = np.zeros(N2)
        for _gk in ("shaft", "sleeve"):
            if _gk in _Msig_grp:
                _dgs += np.asarray(_Msig_grp[_gk].diagonal()).ravel()
        _wci = _dgs[_ci]
        _rem = float(np.sqrt(np.sum(_wci * (_x - _xm) ** 2))
                     / max(np.sqrt(np.sum(_wci * _x ** 2)), 1e-300))
        log.info("P2 eddy warm-up: shaft/sleeve start from the mean of "
                 "their pole-pair images (q = %d, sigma %+d, %d dofs; the "
                 "non-periodic imprint removed = %.3g of the sigma-weighted "
                 "state)", int(_q), int(_sig), _ci.size, _rem)
        return _out, {"q": int(_q), "sigma": int(_sig), "dofs": int(_ci.size),
                      "removed_rel": float("%.4g" % _rem)}
    _n_warm = 0                     # warm-up frames actually solved (all marches)
    _dm_moved_in_warm = False       # Br ratcheted during the current warm-up window
    _warm_resid = None              # remaining transient at the handoff [rel]
    _warm_tau_s = None              # fitted settling time constant [s], if clean
    _warm_extended = False          # the probe was not enough → cap march ran
    # ── THE VERDICT, NOT ONLY THE COST (user's sweep, 2026-09-07) ────────────
    # `_n_warm` says how LONG the march was; it does NOT say whether the march
    # WORKED, and the two were being confused.  In the user's 90-point sweep of
    # 2026-09-07 every single point came back with eddy_warmup_frames = 57
    # (= 2 probe + the handoff frame + one whole 54-step extension period), i.e.
    # every probe failed and every march then ran to its one-shot cap — and
    # nothing in the point said whether the SECOND test, at the end of that
    # extension, passed.  The points whose aluminium shaft tube sits closest to
    # the field then reported shaft eddy losses jumping 504 W → 3558 W → 6652 W
    # between neighbouring air gaps (magnet 24 mm, gap 2.6 / 3.1 / 1.6 mm), and
    # those numbers drove η and P_loss on the chart; they were read as
    # demagnetisation.  A start-up transient is not an answer, so the verdict
    # travels with the numbers it corrupts.
    #   None  → no coupled-eddy march ran at all (no eddy, or magnetostatic
    #           frames, which are settled by construction) → reported settled.
    #   True  → the quiet test passed at the handoff.
    #   False → the march ended at its maximum allowed length (the one-shot
    #           extension to `_eddy_cap`, an SB_EDDY_WARM pin, or the voltage
    #           settling schedule) with the test still failing → CAPPED.
    # EDDY SIDE ONLY, deliberately: `_quiet` below also goes False when merely
    # the Br ratchet moved inside the warm-up window, which the log itself
    # calls an economy note rather than an alarm, and which says nothing about
    # P_mag / P_shaft riding a transient.
    _warm_quiet = None
    # ── THE Br RATCHET MAY ONLY SEE A SETTLED FIELD ──────────────────────────
    # User, 2026-09-05: "the second run always differs from the first".  Two
    # identical Runs of the Ø200 12s/10p at 470.2 A / 20000 rpm / 36 steps with
    # coupled eddy + demag gave T_avg 237.22 vs 244.61 N·m (+3.1 %), Br kept
    # 91.4 vs 98.5 %, Ld 0.055 vs 0.044 mH, rotor heat 837 vs 713 W; a third run
    # gave 232.49.  CAUSE: the irreversible ratchet (`_dmst.update`) ran on
    # EVERY solved frame, the eddy warm-up march at θ<0 included — and that
    # march IS the σ·∂A/∂t start-up transient.  Its ∂A/∂t is whatever the
    # history was seeded with (zero on a cold start, the previous run's settled
    # field when `_SB_WARM_CACHE` / config/.warm_cache.npz seeds it), so the
    # WORST demagnetising field the monotone ratchet burned in permanently came
    # from a field the machine was never in, and it was a DIFFERENT such field
    # in every run.  Everything downstream of Br — torque, Ld/Lq, rotor loss —
    # inherited that irreproducibility.
    #
    # THE RULE: on the θ<0 eddy path the ratchet is FROZEN for the whole warm-up
    # (probe + every extension), and the handoff is followed by a dedicated
    # DEMAG PRE-PASS — one full electrical period, eddy history continued, the
    # ratchet ON, the frames still discarded — before the reported window opens.
    # That is structurally the same two-stage scheme the non-eddy path has had
    # since the settling pass was added (`_dmskip` above: one settling period,
    # then a clean measurement pass), so both paths now measure a magnet that
    # stopped moving, judged on a field that stopped moving.
    #
    # UNCHANGED PATHS: no eddy (the ratchet has no start-up transient to see —
    # a magnetostatic frame is settled by construction) and VOLTAGE drive (it
    # has no θ<0 march at all; its settling prefix is part of a precomputed
    # per-frame schedule that cannot be spliced, so it keeps today's behaviour).
    #
    # ── ONCE PER SWEEP (user 2026-09-06) ─────────────────────────────────────
    # "the demagnetization pass is done once per sweep only".  When
    # the warm cache handed this run a Br map (`_dm_seeded`, sweep mode only —
    # see the seed block above), the pre-pass has ALREADY been paid for by the
    # point that published it and the magnet arrives settled: its length here
    # is 0.  The ratchet still stays FROZEN through the warm-up probe — the
    # seed is projected onto a different mesh and the probe is exactly the
    # transient of that projection relaxing, which is not a field the machine
    # was ever in — and turns ON at the handoff, i.e. from the first REPORTED
    # frame.  So the splice below costs one re-solved frame instead of a whole
    # electrical period, and the reported window is still measured on a magnet
    # the ratchet is actively watching.
    _dm_freeze_warm = bool(demag and _dmst is not None and eddy and not _vdrive)
    _dm_ratchet = not _dm_freeze_warm   # may `_dmst.update` be applied at all?
    _dm_pre_len = (0 if _dm_seeded
                   else (int(n_steps_per_period) if _dm_freeze_warm else 0))
    _dm_prepass = False             # the pre-pass has been spliced in
    _dm_pre_logged = False          # its one summary line has been emitted
    _n_dmpre = 0                    # pre-pass frames solved (ratchet active)
    # ── DEMAG FIXED POINT (shared by the march and TDM; Codex review and owner
    # 2026-09-30).  The ratchet is irreversible, so one pre-pass period does not
    # make the reported period steady: on the L13 at 210.7 °C its first reported
    # period read 1.8 % more torque than its third.  The pre-pass is therefore
    # REPEATED — each further period continuous in time (the eddy state carried
    # one period back by the splice, the Br map by the same pole-pair relabelling)
    # — until one whole period moves Br by no more than SB_DEMAG_SETTLE_TOL
    # (default 1e-3: per magnet the area-weighted mean |ΔBr|/Br0, worst magnet —
    # the magnet flux, hence torque, may move <= 0.1 % in a period, the owner's
    # torque tolerance), at most SB_DEMAG_PREPASS_MAX periods (default 8).  Then
    # the REPORTED period is measured the same way (`demag_settle` in the result):
    # a run whose Br still moves there is labelled demag_settled False /
    # steady_state False, never silently reported as a steady state.
    #
    # PER ELEMENT: A WARNING (owner 2026-10-04).  Demag steadiness is judged
    # by its effect on the observables: the area mean above, the observable
    # drift between the last two pre-pass periods and the rotor-image history
    # (see `_dm_pass_ok`).  A single element still moving more than
    # SB_DEMAG_SETTLE_ELEMENT_TOL (1e-2 of Br0) in a period is recorded
    # (`element_warning`) and named in the note, but does not make the run
    # non-steady — on the L13 rated duty one element at 1.08 % held both
    # methods "not steady" while torque drifted 1.3e-4 and ripple 0.019 pp.
    # The same rule in both methods; the ratchet physics is unchanged.
    from motor_ai_sim.simulation.time_periodic import (
        DEMAG_SETTLE_TOL as _DM_TOL0, DEMAG_SETTLE_ELEMENT_TOL as _DM_ETOL0,
        demag_settled as _demag_settled_rule,
        demag_element_warning as _demag_element_warning)
    _dm_settle_tol = float(_os_sb.environ.get("SB_DEMAG_SETTLE_TOL", "") or _DM_TOL0)
    _dm_settle_etol = float(_os_sb.environ.get("SB_DEMAG_SETTLE_ELEMENT_TOL", "")
                            or _DM_ETOL0)
    _dm_prepass_max = max(1, int(_os_sb.environ.get("SB_DEMAG_PREPASS_MAX", "8") or 8))

    def _dm_is_settled(chg):
        return _demag_settled_rule(chg, _dm_settle_tol)
    _dm_passes: List[Dict[str, Any]] = []   # one record per pre-pass period
    _dm_pass_br0 = None             # Br at the start of the current pre-pass period
    _dm_pass_done = False           # the march's pre-pass iteration has ended
    _dm_rep_br0 = None              # Br when the reported window opened
    _dm_shared: Dict[str, Any] = {}

    def _dm_areas():
        if "areas" not in _dm_shared:
            _dm_shared["areas"] = _triangle_areas(half["r"]["mesh"])
        return _dm_shared["areas"]

    def _dm_change(b0, b1):
        from motor_ai_sim.simulation.time_periodic import br_change as _brc
        return _brc(_dmst.mags, b0, b1, _dm_areas())

    def _dm_relabel(br):
        """Br carried one electrical period back — the same pole-pair map as
        the eddy splice (`_period_shift`), on the magnet-element centroids."""
        if "relabel" not in _dm_shared:
            _el = np.concatenate([np.asarray(_d["idx"], int) for _d in _dmst.mags])
            _cen = np.asarray(half["r"]["mesh"].p, float)[
                :, np.asarray(half["r"]["mesh"].t)[:, _el]].mean(axis=1)
            _sg = (1.0 if (float(period_mech) * float(n_periods)
                           / max(float(n_total), 1.0)) >= 0.0 else -1.0)
            _mp, _mi = _period_shift_map(
                _cen, float(np.sqrt(np.min(_dm_areas()))), n_sectors=int(NS),
                bc_sign=int(_bc_sign), rot_rad=-_sg * math.radians(float(period_mech)))
            if _mp is None or np.unique(_mp[0]).size != _el.size:
                log.warning("P2 demag: magnet elements not pole-pair periodic (%s) — "
                            "further pre-pass periods keep the Br labels", _mi)
                _dm_shared["relabel"] = None
            else:
                _dm_shared["relabel"] = (_el, np.asarray(_mp[0], int))
        _r = _dm_shared["relabel"]
        if _r is None:
            return np.array(br, float, copy=True)
        from motor_ai_sim.simulation.time_periodic import relabel_br as _rlb
        return _rlb(br, _r[0], _r[1])

    # ── DEMAG ASYMPTOTE CHECKS (third Codex review, 2026-10-04, finding 3) ────
    # A per-period Br change does not bound the drift still to come across the
    # ROTOR-IMAGE HISTORY: on a fractional-slot rotor each magnet meets, at the
    # dangerous instant, one of L pole-pair positions per electrical period (L =
    # the order of the period relabelling on the magnet elements: 7 on a
    # 12s14p sector), so the irreversible asymptote is reached only once every
    # magnet has visited every image — where every magnet carries the same Br
    # map up to the pole image (the element-wise image minimum).  So a demag
    # period is settled only when, besides the Br change of the period:
    #   * the IMAGE HISTORY is complete: L pre-pass periods have run, or Br
    #     already equals its image minimum within the settle tolerances (area
    #     mean 1e-3, element 1e-2); and
    #   * the OBSERVABLE DRIFT between the last two pre-pass periods — mean
    #     torque and ripple of the frame torque (Coulomb when the run reports
    #     Coulomb, else the frame loop's Maxwell series) — is below ESTIMATE_SAFETY
    #     of the owner's terms (torque 0.1 %, ripple max(0.05 pp, 1 % of it)).
    # Shared by both methods; a reported period that misses any of them is
    # steady_state False with the numbers in the note.
    _dm_tq_cur: Dict[int, Tuple] = {}
    _dm_shortcut_used = False      # the demag shortcut ran (TDM, optimizer candidates)

    def _dm_frame_torque(A, Is, theta_rad):
        """What the REPORTED torque method needs of one frame (fourth Codex
        review: the drift is measured with the run's own torque method, not
        the raw Maxwell series): the Coulomb virtual-work torque, and for the
        hybrid method the Maxwell torque, the flux linkages, the currents and
        the rotor angle (`sb_postproc.space_vector_hybrid_torque`)."""
        _tc = None
        if torque_method == "coulomb" and _ftq2.coulomb:
            _tc = _frame_torques(_ftq2, A)["coulomb_Nm"]
        _q = _psi2(A)
        return (_tc, float(_torque2(A) * NS), float(_q[0]), float(_q[1]),
                float(_q[2]), float(Is['A']), float(Is['B']), float(Is['C']),
                float(theta_rad))

    def _dm_take_torque():
        """Mean torque and p-p ripple [%] of the pre-pass period that just
        ended (its frames' final fields), by the REPORTED torque method, and
        reset."""
        _rows = [_dm_tq_cur[_k] for _k in sorted(_dm_tq_cur)]
        _dm_tq_cur.clear()
        if not _rows:
            return None
        if all(_r[0] is not None and math.isfinite(_r[0]) for _r in _rows):
            _a = np.asarray([_r[0] for _r in _rows], float)
            _meth = "coulomb_virtual_work"
        else:
            _c = list(zip(*_rows))
            _wf, _meth = _space_vector_hybrid_torque(
                list(_c[2]), list(_c[3]), list(_c[4]), list(_c[5]), list(_c[6]),
                list(_c[7]), list(_c[1]), pole_pairs, n_parallel=int(n_parallel),
                zero_sequence=bool(getattr(_src, "zero_sequence_path", False)),
                mechanical_angle_rad=list(_c[8]))
            _a = np.asarray(_wf, float)
        _m = float(_a.mean())
        return {"T_mean": _m, "ripple_pct": 100.0 * float(_a.max() - _a.min())
                / max(abs(_m), 1e-300), "frames": int(_a.size),
                "torque_method": _meth}

    def _dm_image():
        """(image maps, cycle length L) of the magnets, cached."""
        if "image" not in _dm_shared:
            from motor_ai_sim.simulation.time_periodic import (
                magnet_image_maps as _mim)
            _cen = np.asarray(half["r"]["mesh"].p, float)[
                :, np.asarray(half["r"]["mesh"].t)].mean(axis=1)
            try:
                _maps, _minfo = _mim(_dmst.mags, _cen, _dm_areas(), int(NS),
                                     int(_bc_sign),
                                     math.radians(0.5 * float(period_mech)),
                                     int(_poles_per_sector))
            except Exception as _e_im:       # noqa: BLE001 — recorded, judged below
                _maps, _minfo = None, {"error": str(_e_im)}
            _dm_relabel(_br_glob)            # builds the relabel map
            _rl = _dm_shared.get("relabel")
            _L = None
            if _rl is not None:
                _perm = np.asarray(_rl[1], int)
                _seen = np.zeros(_perm.size, bool)
                _L = 1
                for _i0 in range(_perm.size):
                    if _seen[_i0]:
                        continue
                    _c, _j = 0, _i0
                    while not _seen[_j]:
                        _seen[_j] = True
                        _j = int(_perm[_j])
                        _c += 1
                    _L = _L * _c // math.gcd(_L, _c)
            _dm_shared["image"] = (_maps, _L, _minfo)
        return _dm_shared["image"]

    def _dm_image_check(n_periods):
        """Is the rotor-image history complete?  (record, ok)"""
        from motor_ai_sim.simulation.time_periodic import (
            image_min_br as _imb, br_change as _brc)
        _maps, _L, _minfo = _dm_image()
        _rec: Dict[str, Any] = {"cycle_periods": _L, "pre_pass_periods": int(n_periods)}
        if _maps is None:
            _rec["error"] = "no pole image map of the magnets (%s)" % (_minfo,)
            _rec["complete"] = bool(_L is not None and n_periods >= _L)
            return _rec, _rec["complete"]
        _gap = _brc(_dmst.mags, _imb(_dmst.mags, _br_glob, _maps), _br_glob,
                    _dm_areas())
        _rec["image_gap"] = {"per_magnet_mean_max": _gap["per_magnet_mean_max"],
                             "element_max": _gap["element_max"]}
        # complete ONLY after L pre-pass periods — every magnet has met the
        # dangerous instant at every rotor image (fourth Codex review: a small
        # area-mean image gap after a few quiet periods is not that history;
        # the gap is recorded, not a shortcut)
        _ok = bool(_L is not None and n_periods >= _L)
        _rec["complete"] = _ok
        return _rec, _ok

    def _dm_drift(passes):
        """The observable drift between the last two pre-pass periods."""
        from motor_ai_sim.simulation.time_periodic import owner_limits as _olim
        _t = [p_.get("torque") for p_ in passes[-2:]]
        if len(_t) < 2 or any(x is None for x in _t):
            return {"ok": False, "why": "fewer than two pre-pass periods with torque"}
        _lim = _olim(_t[1]["ripple_pct"])
        _dT = abs(_t[1]["T_mean"] - _t[0]["T_mean"]) / max(abs(_t[1]["T_mean"]), 1e-300)
        _dR = abs(_t[1]["ripple_pct"] - _t[0]["ripple_pct"])
        return {"T_mean_rel": _dT, "ripple_pp": _dR, "tol_T_rel": _lim["T_rel"],
                "tol_ripple_pp": _lim["ripple_pp"],
                "ok": bool(_dT <= _lim["T_rel"] and _dR <= _lim["ripple_pp"])}

    def _dm_pass_ok(rec, passes):
        """The pre-pass iteration's stop rule (both methods): this period's
        Br change settled AND the image history complete AND the observable
        drift below the safety fraction.  Annotates ``rec``."""
        _img, _img_ok = _dm_image_check(len(passes))
        _dr = _dm_drift(passes)
        rec["image_history"] = _img
        rec["drift"] = _dr
        rec["element_warning"] = _demag_element_warning(rec, _dm_settle_etol)
        return bool(_dm_is_settled(rec) and _img_ok and _dr["ok"])

    def _dm_cap():
        """The pre-pass cap: SB_DEMAG_PREPASS_MAX when set, else enough
        periods to complete the image history (at least 8)."""
        if _os_sb.environ.get("SB_DEMAG_PREPASS_MAX"):
            return int(_dm_prepass_max)
        _L = _dm_image()[1]
        return int(max(_dm_prepass_max, (_L or 0)))
    # ── WARM-UP IN THE COMBINED (eddy + conducting rotor + voltage) MODE ──────
    # A voltage run does NOT get the θ<0 probe march, and it does not need one:
    # it already prepends a SETTLING PREFIX of whole electrical periods (_vskip
    # frames, ten of them by default and two under PWM) whose frames are solved
    # and then discarded, and with the conducting rotor in the Newton those
    # frames march the σ·∂A/∂t history exactly as the probe would — only far
    # longer than the probe's 2 frames + one-period cap.  So the prefix IS the
    # eddy warm-up, and what is missing is only the MEASUREMENT: the quiet test
    # at the handoff, the frame count and the residual that say whether P_mag /
    # P_shaft are still riding a decaying start-up transient.  That is what the
    # block below adds, and it is why there is no extension path here — the
    # warm-up cannot run away, its length is the settle schedule.
    #
    # PWM MIXED-RESOLUTION SETTLING: the coarse settle frames solve the FULL
    # eddy dynamics (ve_newton rebuilds Msd_k = Msig/Δt_k and Sdt_k = S_raw·Δt_k
    # from the frame's own step, so a coarse step is a coarse magnetodynamic
    # step, not a magnetostatic one).  They must — the eddy state is the slowest
    # thing in the run and it is precisely what the settle is for; the carrier
    # ripple they skip is a forced response the fine pre-roll regenerates in a
    # couple of carriers.  The GAUGE below therefore reads the coarse frames
    # (uniform Δt, uniform rotor spacing, ~7/8 of the prefix) rather than mixing
    # them with the handful of fine pre-roll frames.
    #
    # WHAT THE RESIDUAL MEANS UNDER PWM — MEASURED, because it does not mean
    # what it says.  eddy_settle_resid kills the 6th-harmonic ANGULAR ripple by
    # averaging in blocks of n_steps/6 before it fits the decaying tail; a
    # chopped run carries a CARRIER ripple on top, and n_steps/6 is not a whole
    # number of carriers (40 frames = 1.82 carriers at 240 steps / 11 carriers),
    # so the block means keep some of it and the Aitken tail reads that leftover
    # as decay.  Measured on ciano14_40_new at 16.68 kHz, 240 steps, rotor eddy
    # on: 2 settle periods (480 frames) -> 8.81 %, 6 settle periods (1440
    # frames) -> 8.91 %, with P_mag identical at 4.678 W to four significant
    # figures.  Three times the warm-up does not move either number: there is no
    # transient left, only ripple the gauge cannot average away.
    #
    # THE FIX (this block): under an all-fine chopped drive the gauge block is
    # made CARRIER-COMMENSURATE by passing 6·nspp as the "period", so
    # eddy_settle_resid's n/6 blocks become one whole ELECTRICAL period each —
    # and one electrical period holds a whole number of carriers by the
    # synchronous snap, so the carrier ripple cancels inside every block mean.
    # eddy_settle_resid caps its block at n//3, so this needs >= 3 settle
    # periods of samples; below that the gauge falls back to the old width and
    # the warning keeps calling the number an upper bound (measured basis
    # above).  eddy_settle_resid itself is untouched — its signature and its
    # pinned test behaviour stand.
    _ve_warm = bool(eddy and _vdrive and rotor_eddy and _vskip >= 3)
    _ve_gauge_fine = (False if _sched_mixed else True)   # which frames to sample
    _ve_gauge_nspp = int(_c_nspp if _sched_mixed else _v_nspp)
    _ve_gauge_carrier_ok = bool(
        _carriers and not _sched_mixed and int(_v_settle_periods) >= 3)
    if _ve_gauge_carrier_ok:
        _ve_gauge_nspp = 6 * int(_v_nspp)
    _ve_gauge_dt = float(_sched_dt[0]) if _sched_dt else float(dt)
    _warm_done = not (eddy and not _vdrive) and not _ve_warm  # handoff accepted
    # previous-frame A per dof (material frame).  Zero is not a field the
    # machine was ever in, so under voltage drive — where the phasor
    # initialiser already produced the operating-point field at θ_eff = 0 —
    # seed it with that instead of a fake step from nothing.  Either seed is
    # inside the discarded settling window; this one just does not spend the
    # first frames unwinding an ∂A/∂t that never happened.
    _Aed_prev = (_Aop.copy() if (eddy and _vdrive) else np.zeros(N2))
    # ── SECOND-ORDER TIME INTEGRATION OF σ·∂A/∂t (2026-09-25) ────────────────
    # Backward Euler read every conductive-body loss low at the default 36
    # steps/period, by O(Δt): L155 rated magnets −10.5 % against the Δt → 0
    # extrapolation, sleeve −4 %, copper AC −2.5 %, torque ripple 25-30 % low
    # (docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md §7.1).  The eddy term
    # is now BDF2 on the ACTUAL step sequence (variable-step coefficients, see
    # p2_drive.bdf2_history): A-stable and stiffly decaying, so the saturating
    # iron and the algebraic (σ = 0) rows are damped instead of ringing, as
    # Crank–Nicolson would on them.  A march starts with ONE backward-Euler
    # step (no second history level yet: cold/static start, voltage initialiser,
    # cold retry, a step-ratio jump beyond the BDF2 stability bound) and is
    # BDF2 from the next step on.  Splices (warm-up extension, demag pre-pass)
    # carry BOTH history levels through the exact pole-pair map, so they stay
    # continuous in time and second order.  SB_EDDY_BE=1 restores backward
    # Euler everywhere (bit-for-bit the pre-2026-09-25 march) for A/B.
    # docs/EDDY_TIME_INTEGRATION_2026-09-25.md.
    _eddy_bdf2 = bool(eddy) and _os_sb.environ.get("SB_EDDY_BE") != "1"
    # WHERE THE JOULE LOSS IS SAMPLED.  BDF2's own derivative at t_k over-reads
    # |∂A/∂t| of an under-resolved harmonic by ~(ωΔt)²/3 (L155 rated magnets
    # +7 % at 36 steps/period).  The loss is therefore sampled at the step
    # MIDPOINT t_{k−½}: ∂A/∂t by the centred difference (A_k − A_{k−1})/Δt of
    # the BDF2 states, U_b from the body's own constraint at that instant
    # (S_b·U_b = I_b(t_{k−½}) + g_b·∂A/∂t, I_b the mean of the two solved net
    # currents) — second order, error ~(ωΔt)²/24 on a resistance-limited body,
    # the same midpoint convention the CN step voltage uses.  The step itself
    # is unchanged.  SB_EDDY_LOSS_AT=step samples BDF2's own derivative at t_k
    # (A/B only).  docs/EDDY_TIME_INTEGRATION_2026-09-25.md §1.
    _ed_loss_mid = (_eddy_bdf2
                    and _os_sb.environ.get("SB_EDDY_LOSS_AT", "mid") != "step")
    _Ib_prev = None                # per-constraint net current at t_{k−1}
    _Ib_prev2 = None               # …and at t_{k−2}
    _BDF2_MAX_RATIO = 1.8          # variable-step BDF2 zero/A-stability bound
    _Aed_prev2 = None              # A_{k−2}: None → next step is backward Euler
    _hed_prev = None               # the step h_{k−1} between A_{k−2} and A_{k−1}
    _ed_ts = {"scheme": ("bdf2" if _eddy_bdf2 else "backward_euler"),
              "loss_sampled_at": ("step_midpoint" if _ed_loss_mid
                                  else "step_end"),
              "be_steps": 0, "bdf2_steps": 0, "ratio_min": None,
              "ratio_max": None}
    _Ued = np.zeros(len(_ed_con))         # per-body conductor voltages
    # ── TIME-PERIODIC (TDM) STEADY STATE (eddy_method="tdm", 2026-09-30) ────
    # The orbit the warm-up march converges to, solved directly: every frame
    # of a (half) electrical period at once, the BDF2 history of the first two
    # closed by the exact (anti)periodic map (simulation/time_periodic.py).
    # Then the frame loop below marches ONE period from that orbit — the
    # reported window, with every per-frame quantity computed exactly as
    # after a settled warm-up — and no warm-up frames at all.  The demag
    # ratchet is not periodic: it runs as a pre-pass ON the orbit (the full
    # period, or the owner's 1/6-period shortcut), and the orbit is re-solved
    # on the ratcheted magnet.  docs/TDM_PROTOTYPE_2026-09-30.md.
    _tdm_info: Optional[Dict[str, Any]] = (
        None if not _tdm_fb else {"failed": _tdm_fb["note"],
                                  "attempts": list(_tdm_fb.get("attempts") or [])})
    _tdm_starts: Optional[Dict[int, np.ndarray]] = None
    _tdm_orbit = None
    _tdm_orbit_U = None
    _tdm_rep: Optional[Dict[int, Tuple[np.ndarray, np.ndarray]]] = None
    _tdm_note = (_tdm_fb["note"] if _tdm_fb else None)   # why TDM was not used
    if not eddy:
        _eddy_method = "march"   # nothing to settle: the question does not arise
    if _eddy_method == "tdm":
        from motor_ai_sim.simulation import time_periodic as _tdm
        from motor_ai_sim.simulation.pardiso_lifetime import (
            release_pardiso as _tdm_release)
        _tdm_t0 = _t.time()
        _tdm_why = _tdm_refusals(
            eddy=bool(eddy), voltage_drive=bool(_vdrive),
            series_paths=bool(eddy and _ed_paths is not None),
            frozen_nu=bool(frozen_nu), mixed_schedule=bool(_sched_mixed),
            bdf2=bool(_eddy_bdf2), n_periods=float(n_periods),
            full_ring=bool(_full_ring), source_name=getattr(_src, "name", None),
            external_excitation=excitation is not None,
            six_phase=bool(six_phase),
            n_steps_per_period=int(n_steps_per_period))
        if _tdm_why:
            # NOT an error (owner 2026-09-30: TDM is the default for every
            # steady-state eddy run): whatever TDM cannot solve is marched,
            # and the result says so in one line
            _tdm_note = ("march: TDM not applicable (%s)" % "; ".join(_tdm_why))
            log.info("P2 eddy: %s", _tdm_note)
            _eddy_method = "march"
    if _eddy_method == "tdm":
        # A failure anywhere in the attempt is NOT repaired in place: it raises
        # TdmAttemptFailed and fem_transient_sliding_band discards this whole
        # solve and runs a clean one (transactional; Codex review 2026-09-30).
        _td_stage = ["setup"]
        _td_fault = str(_os_sb.environ.get("SB_TDM_FAULT", "") or "").strip().lower()

        def _td_mark(stage, label=None):
            """Enter a TDM stage: the Stop checkpoint, and (tests only) the
            SB_TDM_FAULT injection that proves the rollback."""
            _td_stage[0] = stage
            if label:
                _cancel_point(label)
            if _td_fault == stage:
                raise RuntimeError("SB_TDM_FAULT: injected fault at %s" % stage)
        try:
            _td_mark("setup", "TDM set-up")
            _td_nspp = int(n_steps_per_period)
            _td_dth = float(period_mech) * float(n_periods) / float(n_total)
            _td_sgn = 1.0 if _td_dth >= 0.0 else -1.0

            _td_geometry_cache = {}  # per run/mesh, exact integer slip; never cache excitation

            def _td_ops(k):
                """(Pro, free, I_vec, Ist) of frame k — the frame loop's own."""
                _th = (k / n_total) * period_mech * n_periods
                _m = int(round(_th / spacing))
                _the = _m * spacing
                _fbk = _Feedback(k=int(k), theta_prev_deg=_the - _td_dth,
                                 theta_deg=float(_the), t0_s=float(k * dt),
                                 t1_s=float((k + 1) * dt), fine=True, i_abc=None,
                                 psi_abc=None, v_bus=_fb_v_bus)
                _Is = _src.mean_over(_fbk)
                _Iv = np.array([_Is[c["phase"]] * c["Iunit"] if c["key"] == "cu"
                                else 0.0 for c in _ed_con], float)
                if _m not in _td_geometry_cache:
                    _P, _o = _proj.build(_m)
                    _free = np.setdiff1d(np.arange(_P.shape[1]), _o)
                    _td_geometry_cache[_m] = (_P, _free)
                _P, _free = _td_geometry_cache[_m]
                return (_P, _free, _Iv, _Is, _m)

            # the exact rotor maps: one period (pole pair) or one half (one pole)
            _td_edf = np.asarray(b2.element_dofs)
            _td_rdf = np.unique(_td_edf[:, nst:].ravel())
            _td_h = float(np.sqrt(np.min(_triangle_areas(half["r"]["mesh"]))))

            def _td_map(frac, direction):
                _mp, _mi = _period_shift_map(
                    np.asarray(b2.doflocs, float)[:, _td_rdf], _td_h,
                    n_sectors=int(NS), bc_sign=int(_bc_sign),
                    rot_rad=direction * _td_sgn * math.radians(float(period_mech) * frac))
                return _mp, _mi

            def _td_apply(mp, neg, v):
                _jj, _ss = mp
                _o = (-np.asarray(v, float)) if neg else np.array(v, float, copy=True)
                _o[_td_rdf] = (-_ss if neg else _ss) * np.asarray(v, float)[_td_rdf[_jj]]
                return _o

            # The FULL period by default (Codex review 2026-09-30).  The HALF
            # period (anti-periodic, half the frames) is opt-in, SB_TDM_HALF=1,
            # and taken only when the excitation is half-wave symmetric, the
            # frame grid splits the period into two whole halves, the rotor mesh
            # is pole periodic and the magnet source is pole-antisymmetric: its
            # reported period equals the full-period one on the 30 mm fixture
            # (torque to 1e-6), but the DISCRETE orbit is not exactly
            # half-period symmetric there — the reported torque of the second
            # half differs from the first by up to 0.3 %, and the shaft state is
            # 8 % off the anti-periodic image — so the acceptance gate, which
            # compares the reported period with the orbit frame by frame, can
            # certify only the first half of a half-period orbit.
            _td_half_why = None
            _td_half_asked = False
            _td_Nh = _td_nspp // 2
            _td_K_chk = _p2.asmK(nu_base2)     # the map checks' stiffness (linear ν)
            if (_os_sb.environ.get("SB_TDM_HALF", "0") != "1"
                    or (_tdm_ctl or {}).get("period") == "full"):
                _td_half_why = (
                    "the previous attempt was rejected; the retry takes the full "
                    "period"
                    if (_tdm_ctl or {}).get("period") == "full"
                    and _os_sb.environ.get("SB_TDM_HALF", "0") == "1"
                    else "full period (default; SB_TDM_HALF=1 for the half)")
                _td_half_asked = _os_sb.environ.get("SB_TDM_HALF", "0") == "1"
            elif _td_nspp % 2:
                _td_half_asked = True
                _td_half_why = "odd steps per period"
            else:
                _td_half_asked = True
                _Ia = [_td_ops(k)[3] for k in (0, _td_Nh, 1, _td_Nh + 1)]
                _Imx = max(abs(float(_v)) for _d in _Ia for _v in _d.values()) or 1.0
                _asym = max(abs(float(_Ia[i][ph]) + float(_Ia[i + 1][ph]))
                            for i in (0, 2) for ph in "ABC")
                _m0 = int(round(0.0 / spacing))
                _mh = int(round((_td_Nh / n_total) * period_mech * n_periods / spacing))
                if _asym > _tdm.HALF_SYMMETRY_RTOL * _Imx:
                    _td_half_why = ("current not half-wave antisymmetric (%.3g of "
                                    "the peak)" % (_asym / _Imx))
                elif abs((_mh - _m0) * spacing - 0.5 * float(period_mech)) > 1e-9 * abs(
                        float(period_mech)):
                    _td_half_why = "half a period is not a whole number of slip nodes"
            _td_back = _td_fwd = None
            _tdm_fdev = None
            _td_half_chk = None
            if _td_half_why is None:
                _td_back, _mi_b = _td_map(0.5, -1.0)
                _td_fwd, _mi_f = _td_map(0.5, +1.0)
                if _td_back is None or _td_fwd is None:
                    _td_half_why = "rotor mesh not pole periodic (%s)" % (_mi_b,)
                else:
                    # HALF PERIOD ONLY AT ROUND-OFF (second Codex review,
                    # finding 2): the anti-periodic orbit is a fixed point of
                    # the march only when the magnet source AND the operators
                    # are anti-symmetric under "one pole back, negated" EXACTLY.
                    # A discretisation asymmetry (the fixture's magnet source
                    # 7.8e-6, L155 2.2e-5) is not round-off, and there the half
                    # period moved the fixture's shaft loss +0.56 %; so the
                    # source, the stiffness, the sigma-mass, the conductor bodies
                    # and the constrained spaces must all match to
                    # HALF_SYMMETRY_RTOL (1e-12), else the FULL period, with a note.
                    _Nb = _td_Nh
                    _ok_h, _td_half_chk = _tdm.period_map_checks(
                        wrap=lambda v: _td_apply(_td_back, True, v),
                        wrapf=lambda v: _td_apply(_td_fwd, True, v),
                        P_from=_td_ops(_Nb - 1)[0], P_to=_td_ops(-1)[0],
                        P_start=_td_ops(0)[0], P_fwd=_td_ops(_Nb)[0],
                        K=_td_K_chk, Msig=_Msig2, GT=_G2.T.tocsr(), S_raw=_S_con,
                        f_mag=f_mag2,
                        tol={_kk: min(_vv, _tdm.HALF_SYMMETRY_RTOL)
                             for _kk, _vv in _tdm.MAP_CHECK_RTOL.items()})
                    _tdm_fdev = float(_td_half_chk["dev"]["source"])
                    if not _ok_h:
                        _td_half_why = (
                            "not anti-symmetric to round-off (%s; tol %.0e)" % (
                                ", ".join("%s %.3g" % (_kk, _td_half_chk["dev"][_kk])
                                          for _kk in _td_half_chk["failed"]),
                                _tdm.HALF_SYMMETRY_RTOL))
            _td_neg = _td_half_why is None
            if not _td_neg:
                _td_back, _mi_b = _td_map(1.0, -1.0)
                _td_fwd, _mi_f = _td_map(1.0, +1.0)
                if _td_back is None or _td_fwd is None:
                    raise NotImplementedError(
                        "eddy_method='tdm' refused: the rotor dofs are not pole-pair "
                        "periodic (%s) — no exact period map" % (_mi_b,))
            _td_N = _td_Nh if _td_neg else _td_nspp
            # THE MAP CHECKS of the period actually used (second Codex review,
            # finding 7): inverse, BC signs / constrained spaces, operators,
            # conductor bodies and magnet source.  A deviation above
            # MAP_CHECK_RTOL REFUSES TDM here (set-up stage: the run is marched,
            # with the reason in the note).
            _ok_m, _td_map_chk = _tdm.period_map_checks(
                wrap=lambda v: _td_apply(_td_back, _td_neg, v),
                wrapf=lambda v: _td_apply(_td_fwd, _td_neg, v),
                P_from=_td_ops(_td_N - 1)[0], P_to=_td_ops(-1)[0],
                P_start=_td_ops(0)[0], P_fwd=_td_ops(_td_N)[0],
                K=_td_K_chk, Msig=_Msig2, GT=_G2.T.tocsr(), S_raw=_S_con,
                f_mag=f_mag2,
                tol=(None if not _td_neg else
                     {_kk: min(_vv, _tdm.HALF_SYMMETRY_RTOL)
                      for _kk, _vv in _tdm.MAP_CHECK_RTOL.items()}))
            _td_K_chk = None
            log.info("TDM: %s period, %d frames (%s); map checks %s %s",
                     "HALF (anti-periodic)" if _td_neg else "FULL", _td_N,
                     "half-wave symmetric" if _td_neg else _td_half_why,
                     "passed" if _ok_m else "FAILED",
                     {_kk: "%.2g" % _vv for _kk, _vv in _td_map_chk["dev"].items()})
            if _td_half_asked and not _td_neg:
                _tdm_note = "tdm: half period refused (%s) — full period" % (
                    _td_half_why,)
                log.warning("TDM: %s", _tdm_note)
            if not _ok_m:
                _tdm_info = {"map_check": _td_map_chk, "half_check": _td_half_chk,
                             "period": "half_antiperiodic" if _td_neg else "full"}
                raise RuntimeError(
                    "the %s-period map fails its checks (%s) — TDM refused" % (
                        "half" if _td_neg else "full",
                        ", ".join("%s %.3g > %.0e" % (
                            _kk, _td_map_chk["dev"][_kk], _td_map_chk["tol"][_kk])
                            for _kk in _td_map_chk["failed"])))

            def _td_wrap(v):          # state one (half) period BACK
                return _td_apply(_td_back, _td_neg, v)

            def _td_wrapf(v):         # …and FORWARD
                return _td_apply(_td_fwd, _td_neg, v)

            def _td_orbit(k, As):
                """The orbit state at any frame k from the N solved ones."""
                if 0 <= k < _td_N:
                    return As[k]
                if k >= _td_N:
                    return _td_wrapf(_td_orbit(k - _td_N, As))
                return _td_wrap(_td_orbit(k + _td_N, As))

            _td_ops_l = [_td_ops(k) for k in range(_td_N)]
            _td_cond = np.flatnonzero(np.asarray(_Msig2.diagonal()).ravel() > 0.0)
            _td_msd = _tdm.bdf2_msd(_Msig2, dt)
            _td_workers = max(1, int(_os_sb.environ.get("SB_TDM_WORKERS", "1") or 1))
            _td_mklt = _os_sb.environ.get("SB_TDM_MKL_THREADS")
            _td_mklt = int(_td_mklt) if _td_mklt else None
            _td_frames = [
                _tdm.TdmFrame(k, _o[0], _o[1], _o[2], Msd=_td_msd, G=_G2,
                              cond=_td_cond,
                              factor=_tdm.FrameFactor(own=_own_pardiso,
                                                      release=_tdm_release))
                for k, _o in enumerate(_td_ops_l)]
            # the DC coarse space: the solid ring conductors (U = 0 by symmetry)
            _td_coarse = None
            _td_rk = [_gk for _gk in ("shaft", "sleeve") if _gk in _Msig_grp]
            if _td_rk and _os_sb.environ.get("SB_TDM_COARSE", "1") != "0":
                _td_Mr = None
                for _gk in _td_rk:
                    _td_Mr = (_Msig_grp[_gk] if _td_Mr is None
                              else _td_Mr + _Msig_grp[_gk])
                _td_Mr = _td_Mr.tocsr()
                _td_ring = np.flatnonzero(np.asarray(_td_Mr.diagonal()).ravel() > 0.0)
                try:
                    _pm_t, _sg_t = _acc_restrict(
                        _td_rdf, _td_back[0],
                        (-_td_back[1] if _td_neg else _td_back[1]), _td_ring)
                    _q_t, _s_t, _ = _acc_cycles(_pm_t, _sg_t)
                    _td_coarse = {"ring": _td_ring, "Msig": _td_Mr,
                                  "image": (_pm_t, _sg_t, _q_t, _s_t),
                                  "period_s": float(_td_N) * float(dt),
                                  "own": _own_pardiso, "release": _tdm_release}
                except ValueError as _e_c:
                    log.warning("TDM: no DC coarse space (%s)", _e_c)
            _td_K_lin = None if _sat2 else _p2.asmK(nu_base2)
            # the Newton tangent: the march's clamped one (default) or the exact
            # dH/dB (unclamped; still SPD on a monotone B-H curve, and the factor
            # takes LU loudly where it is not)
            _td_tan_mode = str(_os_sb.environ.get("SB_TDM_TANGENT", "clamped")
                               or "clamped").lower()
            _td_tangent = (_tdm.analytic_tangent(_p2, MU0) if _td_tan_mode == "analytic"
                           else (lambda _inf: _p2.tangent2(_inf, clamp=False))
                           if _td_tan_mode == "exact" else _p2.tangent2)
            # THE OWNER'S TERMS for stopping (coordinator 2026-09-30): the SHIPPED
            # torque of the iterate — the frame loop's own Maxwell series
            # (_torque2) and flux linkage (_psi2) through the same
            # sb_postproc.space_vector_hybrid_torque the result uses for an eddy
            # run ("energy_mean+maxwell_ripple") — and its conductor loss.  Only
            # to decide when to stop; the reported numbers come from the march
            # of the reported period.
            _td_wsc = float(NS) * float(p.stack_length)
            _td_zseq = bool(getattr(_src, "zero_sequence_path", False))

            _td_mon_calls = [0]

            def _td_monitor(As, Us, ev):
                _phase_point("eddy warm-up (time-periodic steady state, TDM): "
                             "Newton %d, "
                             "residual %.1e" % (_td_mon_calls[0],
                                                max(_e[2] for _e in ev)))
                _td_mon_calls[0] += 1
                if torque_method == "coulomb" and _ftq2.coulomb:
                    # the REPORTED method: Coulomb virtual work of each frame
                    # (virtual_work_torque.frame_torques, the loop's own call)
                    _tc = [_frame_torques(_ftq2, _A)["coulomb_Nm"] for _A in As]
                    if all(_v is not None and math.isfinite(_v) for _v in _tc):
                        _wf = np.asarray(_tc, float)
                        _tmean = float(_wf.mean())
                        _tpp = float(_wf.max() - _wf.min())
                        return {"T_mean": _tmean, "T_pp": _tpp,
                                "ripple_pct": 100.0 * _tpp / max(abs(_tmean), 1e-300),
                                "P_joule": float(np.mean(_td_solver.joule(As, Us)))
                                * _td_wsc,
                                "torque_method": "coulomb_virtual_work"}
                _Tm, _pa, _pb, _pc, _ia, _ib, _ic, _th = ([] for _ in range(8))
                for _j, _A in enumerate(As):
                    _Tm.append(_torque2(_A) * NS)
                    _q = _psi2(_A)
                    _pa.append(_q[0]); _pb.append(_q[1]); _pc.append(_q[2])
                    _Is = _td_ops_l[_j][3]
                    _ia.append(_Is['A']); _ib.append(_Is['B']); _ic.append(_Is['C'])
                    _th.append(math.radians(_td_ops_l[_j][4] * spacing))
                _wf, _meth = _space_vector_hybrid_torque(
                    _pa, _pb, _pc, _ia, _ib, _ic, _Tm, pole_pairs,
                    n_parallel=int(n_parallel), zero_sequence=_td_zseq,
                    mechanical_angle_rad=_th)
                _wf = np.asarray(_wf, float)
                _tmean = float(_wf.mean())
                _tpp = float(_wf.max() - _wf.min())
                return {"T_mean": _tmean, "T_pp": _tpp,
                        "ripple_pct": 100.0 * _tpp / max(abs(_tmean), 1e-300),
                        "P_joule": float(np.mean(_td_solver.joule(As, Us))) * _td_wsc,
                        "torque_method": _meth}

            _td_GT = _G2.T.tocsr()
            _td_bgrp = [str(_c["key"]) for _c in _ed_con]
            _td_dte_o = _tdm.DTE_FACTOR * float(dt)

            def _td_observe(As, Us, a_m1, a_m2, ks):
                """The owner's observables of a window of frames ``ks`` (states
                As/Us, history a_m1/a_m2 before the first): the REPORTED torque
                method's mean and ripple, and every conductor group's σE²
                [W, machine] — the acceptance gate compares the reported period
                with the orbit on these."""
                _T = None
                _meth = None
                if torque_method == "coulomb" and _ftq2.coulomb:
                    _tc = [_frame_torques(_ftq2, _A)["coulomb_Nm"] for _A in As]
                    if all(_v is not None and math.isfinite(_v) for _v in _tc):
                        _T = np.asarray(_tc, float)
                        _meth = "coulomb_virtual_work"
                if _T is None:
                    _Tm, _pa, _pb, _pc, _ia, _ib, _ic, _th = ([] for _ in range(8))
                    for _A, _k in zip(As, ks):
                        _Tm.append(_torque2(_A) * NS)
                        _q = _psi2(_A)
                        _pa.append(_q[0]); _pb.append(_q[1]); _pc.append(_q[2])
                        _o = _td_ops(_k)
                        _ia.append(_o[3]['A']); _ib.append(_o[3]['B'])
                        _ic.append(_o[3]['C'])
                        _th.append(math.radians(_o[4] * spacing))
                    _wf, _meth = _space_vector_hybrid_torque(
                        _pa, _pb, _pc, _ia, _ib, _ic, _Tm, pole_pairs,
                        n_parallel=int(n_parallel), zero_sequence=_td_zseq,
                        mechanical_angle_rad=_th)
                    _T = np.asarray(_wf, float)
                _tm = float(_T.mean())
                _seq = [a_m2, a_m1] + list(As)
                _P: Dict[str, float] = {}
                for _j in range(len(As)):
                    _gj = _tdm.group_joule(_seq[_j + 2], Us[_j], _seq[_j + 1], _seq[_j],
                                           dte=_td_dte_o, Mg=_Msig_grp, GT=_td_GT,
                                           S_raw=_S_con, body_group=_td_bgrp)
                    for _g, _v in _gj.items():
                        _P[_g] = _P.get(_g, 0.0) + _v
                return {"T_mean": _tm,
                        "ripple_pct": 100.0 * float(_T.max() - _T.min())
                        / max(abs(_tm), 1e-300),
                        "P": {_g: _v / max(len(As), 1) * _td_wsc for _g, _v in _P.items()},
                        "T": [float("%.9g" % _v) for _v in _T],
                        "torque_method": _meth}

            _td_stop = str((_tdm_ctl or {}).get("stop")
                           or _os_sb.environ.get("SB_TDM_STOP", "owner") or "owner").lower()
            _td_solver = _tdm.TimePeriodicEddy(
                monitor=_td_monitor,
                stop_owner=(None if _td_stop == "residual" else
                            {"T_mean_rel": 1e-3, "ripple_pp": 0.05, "P_rel": 5e-3,
                             "rrel_floor": 1e-5}),
                kfun=(_p2.Kpw if _sat2 else (lambda _A: (_td_K_lin, None))),
                tangent=_td_tangent, f_mag=f_mag2, G=_G2, Msig=_Msig2,
                S_raw=_S_con, dt=dt, frames=_td_frames, wrap_back=_td_wrap,
                cond=_td_cond, coarse=_td_coarse,
                tol=float(_os_sb.environ.get("SB_TDM_TOL", "1e-7") or 1e-7),
                max_newton=int(_os_sb.environ.get("SB_TDM_MAX_NEWTON", "25") or 25),
                workers=_td_workers, mkl_threads=_td_mklt,
                # Newton forcing of the wrap GMRES: 0.01 fixed (measured on the
                # Ø40: the space-time Newton contracts ×6 per iteration whatever
                # the forcing — the table-interpolated B-H curve, not the linear
                # solve, sets its rate — and 0.01 needs 23 Krylov iterations
                # where the adaptive 1e-2·rrel needed 62); "adaptive" = that rule
                eta=(None if str(_os_sb.environ.get("SB_TDM_ETA", "0.01")).lower()
                     == "adaptive" else float(_os_sb.environ.get("SB_TDM_ETA", "0.01")
                                              or 0.01)), log=log)
            _tdm_info = {"method": "newton_krylov_shooting_dc_coarse",
                         "period": "half_antiperiodic" if _td_neg else "full",
                         "half_refused": _td_half_why, "frames": int(_td_N),
                         "magnet_source_half_asymmetry": _tdm_fdev,
                         "map_check": _td_map_chk, "half_check": _td_half_chk,
                         "workers": int(_td_workers), "mkl_threads": _td_mklt,
                         "tangent": _td_tan_mode, "eta": _td_solver.eta,
                         "stop": ("state residual < tol" if _td_stop == "residual"
                                  else "state residual < tol, or the owner's terms: "
                                       "T_mean < 0.1 %, ripple < 0.05 pp, eddy loss "
                                       "< 0.5 % between iterates with rrel < 1e-5"),
                         "conductor_dofs": int(_td_cond.size), "t": {},
                         "attempt": (2 if (_tdm_ctl or {}).get("stop") else 1),
                         "previous_attempts": list((_tdm_ctl or {}).get("attempts")
                                                   or [])}
            try:
                # ── the start: the static (∂A/∂t = 0) field of every frame ──────
                _td_mark("static_start", "TDM static start")
                _t_s = _t.time()
                _td_A0: List[np.ndarray] = []
                _td_U0: List[np.ndarray] = []
                _td_prev = None
                # sequential static solves (each warm-started from its neighbour)
                # are cheaper in serial; in parallel every frame starts from
                # frame 0's field at once (measured on the Ø40: 8.5 s sequential,
                # 12.4 s parallel-from-frame-0 with one worker, 6.4 s with four)
                _td_start_mode = str(_os_sb.environ.get(
                    "SB_TDM_START", "static_par" if _td_workers > 1 else "static_seq")
                    or "static_seq").lower()
                _tdm_info["start"] = _td_start_mode
                for _j, (_P, _fr, _Iv, _Is, _m) in enumerate(_td_ops_l):
                    if _td_prev is None:
                        _f_s = (f_mag2 + _Is['A'] * f_coil2['A']
                                + _Is['B'] * f_coil2['B'] + _Is['C'] * f_coil2['C'])
                        _As0 = np.zeros(N2)
                        if _sat2:
                            _As0, _, _, _ = _p2.pic2_sweeps(
                                _P, _fr, np.asarray(_P.T @ _f_s).ravel()[_fr],
                                nu_base2.copy(), _PIC_SEED_MAX, _PIC_SEED_TOL)
                    else:
                        _pd = np.asarray(_P.multiply(_P).sum(axis=0)).ravel()
                        _As0 = _P @ (np.asarray(_P.T @ _td_prev).ravel()
                                     / np.maximum(_pd, 1.0))
                    if _td_prev is not None and _td_start_mode == "project":
                        # frame 0's static field, projected: the periodic Newton
                        # takes the rotation from there (no per-frame static solve)
                        _ok_s, _Ast = True, _As0
                    else:
                        _ok_s, _Ast = _drv.eddy_static_state(
                            _P, _fr, _As0, _Iv, (None if _sat2 else nu_base2),
                            max(int(nonlinear_iterations), 20))
                    if not _ok_s:
                        raise RuntimeError("TDM: the static start field of frame %d "
                                           "did not converge" % _j)
                    _td_A0.append(_Ast)
                    _td_U0.append(_Iv / np.maximum(_S_con, 1e-300))
                    _td_prev = _Ast if _td_prev is None or _td_start_mode != "project" \
                        else _td_prev
                    if _td_start_mode in ("static_par", "static_seq"):
                        # frame 0 done (cold, as above); every other frame's
                        # static field to a loose tolerance: in parallel from
                        # frame 0, or in sequence from its neighbour
                        _td_A0, _ssi = _td_solver.static_start(
                            _Ast, sequential=(_td_start_mode == "static_seq"))
                        _td_U0 = [_o[2] / np.maximum(_S_con, 1e-300)
                                  for _o in _td_ops_l]
                        _tdm_info["static_start"] = _ssi
                        break
                _tdm_info["t"]["static_start"] = _t.time() - _t_s
                _td_mark("newton", "TDM Newton")
                _t_s = _t.time()
                _st = _td_solver.solve(_td_A0, _td_U0)
                _tdm_info["t"]["newton"] = _t.time() - _t_s
                _tdm_info["solve"] = {k_: (list(v_) if isinstance(v_, list) else v_)
                                      for k_, v_ in _st.items() if k_ != "t"}
                _tdm_info["solve"]["t"] = dict(_st["t"])
                _td_cnt0 = {k_: _st.get(k_) for k_ in (
                    "gmres_iterations", "jacobian_factorizations", "back_solves",
                    "residual_evals", "sweeps")}
                if not _st.get("converged"):
                    raise RuntimeError(
                        "TDM: the periodic Newton did not converge (max frame rrel "
                        "%.3e after %d iterations)" % (_st.get("rrel_max", float("nan")),
                                                      _st.get("newton_iterations", -1)))
                _td_As = [np.array(_fr_.A, float) for _fr_ in _td_frames]
                _td_Us = [np.array(_fr_.U, float) for _fr_ in _td_frames]
                # DIAGNOSTIC: the forward map (used only for the START of the
                # reported frames beyond the first (half) period) against the
                # back map the Newton closes the orbit with, on the orbit state,
                # in the conductor sigma-norm.  The closure itself (both BDF2
                # history levels) is what the acceptance gate verifies.
                _td_v0 = _td_As[0]
                _td_dd = _td_wrapf(_td_wrap(_td_v0)) - _td_v0
                _tdm_info["maps_inverse_dev"] = float(
                    np.sqrt(max(float(_td_dd @ (_Msig2 @ _td_dd)), 0.0))
                    / max(float(np.sqrt(max(float(_td_v0 @ (_Msig2 @ _td_v0)), 0.0))),
                          1e-300))
                # ── demag: the ratchet ON the orbit, then the orbit re-solved ────
                if demag and _dmst is not None and _dmst.active and not _dm_seeded:
                    _td_mark("demag", "TDM demag pre-pass")
                    _t_s = _t.time()
                    _td_dmode = _tdm_demag
                    _td_dm = {"mode": _td_dmode}
                    _td_dte = _tdm.DTE_FACTOR * float(dt)

                    def _td_solve_frame(k, A_start, Ahist):
                        _phase_point("eddy warm-up (TDM): demag pre-pass on "
                                     "the periodic orbit "
                                     "(TDM, %s): frame %d" % (_td_dmode, k))
                        _P, _fr, _Iv, _Is, _m = _td_ops(k)
                        _pd = np.asarray(_P.multiply(_P).sum(axis=0)).ravel()
                        _Ast = _P @ (np.asarray(_P.T @ A_start).ravel()
                                     / np.maximum(_pd, 1.0))
                        _ok, _A, _U, _r, _n = _drv.eddy_solve(
                            _P, _fr, _Ast, np.zeros(len(_ed_con)), _Iv, Ahist,
                            (None if _sat2 else nu_base2),
                            max(int(nonlinear_iterations), 20), dte=_td_dte)
                        if not _ok:
                            raise RuntimeError("TDM demag pre-pass: bordered Newton "
                                               "did not converge at frame %d" % k)
                        # the frame's torque for the drift check (the last
                        # re-solve of a frame overwrites the earlier ones)
                        _dm_tq_cur[int(k)] = _dm_frame_torque(
                            _A, _Is, math.radians(_m * spacing))
                        return _A

                    def _td_rebuild_fmag():
                        nonlocal f_mag2
                        _mx_all[nst:] = _Mx_glob * _br_glob
                        _my_all[nst:] = _My_glob * _br_glob
                        f_mag2 = asm(_msrc, b2, mx=b2_0.interpolate(_mx_all),
                                     my=b2_0.interpolate(_my_all))
                        _drv.f_mag = f_mag2
                        _td_solver.f_mag = f_mag2

                    def _td_Bel(A):
                        _bxq, _byq, _dxq = _p2_B_at_quad(b2, A)
                        _ar = _dxq.sum(axis=1)
                        return ((_bxq * _dxq).sum(axis=1) / np.maximum(_ar, 1e-30),
                                (_byq * _dxq).sum(axis=1) / np.maximum(_ar, 1e-30))

                    def _td_ratchet(A):
                        _bx, _by = _td_Bel(A)
                        _hit = _dmst.update(_bx[nst:], _by[nst:])
                        if _hit:
                            _td_rebuild_fmag()
                        return _hit

                    def _td_orbit_start(k):
                        return _td_orbit(k, _td_As)

                    _br_pristine = _br_glob.copy()
                    _ar_r = _triangle_areas(half["r"]["mesh"])
                    _td_mapinfo = None
                    _td_maps = None
                    if _td_dmode in ("shortcut", "full"):
                        _cen_r2 = np.asarray(half["r"]["mesh"].p, float)[
                            :, np.asarray(half["r"]["mesh"].t)].mean(axis=1)
                        _td_maps, _td_mapinfo = _tdm.magnet_image_maps(
                            _dmst.mags, _cen_r2, _ar_r, int(NS), int(_bc_sign),
                            math.radians(0.5 * float(period_mech)),
                            int(_poles_per_sector))
                    if _td_dmode == "shortcut" and _td_maps is not None:
                        # THE DEMAG SHORTCUT — the method of OPTIMIZER CANDIDATES
                        # only (owner 2026-10-04; requested by refine_proc, never
                        # by the environment).  Not qualified for reported
                        # numbers: every reported run (EM tab, coupled loop,
                        # passport, MCP, reports) and the optimizer's final
                        # verification take the full pre-pass.  The windows run
                        # below, once the orbit re-solve is defined.
                        _td_dm["experimental"] = True
                        _dm_shortcut_used = True
                        _tdm_note = ("tdm: demag SHORTCUT (adaptive two 1/6-period "
                                     "windows; optimizer candidates only, not "
                                     "qualified) — the full pre-pass gives the "
                                     "reported numbers")
                        log.warning("TDM: %s", _tdm_note)
                    else:
                        if _td_dmode == "shortcut":
                            _td_dm["refused"] = ("no pole image map of the magnets "
                                                 "(%s) — full pre-pass" % (_td_mapinfo,))
                            log.warning("TDM demag shortcut refused (%s): full "
                                        "pre-pass instead", _td_mapinfo)
                    _td_rs = {"converged": True, "newton_iterations": 0, "solves": 0}
                    _td_cnt_prev = dict(_td_cnt0)

                    def _td_resolve_orbit(why):
                        """The orbit on the CURRENT Br (warm start from the last
                        one); counts only this re-solve's work."""
                        nonlocal _td_As, _td_Us
                        _td_mark("resolve")
                        _t_r = _t.time()
                        _st2 = _td_solver.solve(_td_As, _td_Us)
                        _tdm_info["t"]["resolve"] = (_tdm_info["t"].get("resolve", 0.0)
                                                     + _t.time() - _t_r)
                        _td_rs["solves"] += 1
                        _td_rs["newton_iterations"] += int(_st2.get("newton_iterations", 0))
                        _td_rs["rrel_max"] = float(_st2.get("rrel_max", 0.0))
                        for k_ in _td_cnt_prev:
                            _v2 = int(_st2.get(k_, 0) or 0)
                            _td_rs[k_] = _td_rs.get(k_, 0) + _v2 - int(_td_cnt_prev[k_] or 0)
                            _td_cnt_prev[k_] = _v2
                        if not _st2.get("converged"):
                            _td_rs["converged"] = False
                            raise RuntimeError("TDM: the orbit re-solve on the "
                                               "ratcheted magnet (%s) did not "
                                               "converge" % why)
                        _td_As = [np.array(_fr_.A, float) for _fr_ in _td_frames]
                        _td_Us = [np.array(_fr_.U, float) for _fr_ in _td_frames]

                    if _td_dmode == "shortcut" and _td_maps is not None:
                        # ADAPTIVE TWO WINDOWS (owner 2026-10-04; Codex docs
                        # fem-tdm-l155-adaptive-windows-2026-10-03 and
                        # fem-tdm-repeat-short-windows-2026-10-02): on the
                        # CURRENT orbit and Br, the (magnet, instant) with the
                        # largest predicted Br loss (`predicted_demag`, the
                        # ratchet rule without the update) is located; a window
                        # of exactly 1/6 period around it is marched with the
                        # ratchet; that magnet's Br map is carried to every pole
                        # image and taken element-wise with np.minimum against the
                        # current Br (irreversible: Br never rises, checked); the
                        # orbit is re-solved — and the selection is REPEATED on
                        # the updated orbit for the second window.  The reported
                        # period's settle checks are the shared ones (Br change,
                        # and the drift of the reported torque between the two
                        # windows' orbits).
                        # exactly 6 frames for 1/6 of 36 (the old ceil(0.1666667 N)
                        # gave 7)
                        _W = _tdm.demag_window_frames(
                            _td_nspp, _os_sb.environ.get("SB_TDM_DEMAG_WINDOW") or "1/6")
                        _td_dm["window_frames_count"] = int(_W)
                        _td_dm["windows"] = []
                        _td_dm["march"] = {"frames": 0, "solves": 0, "ratchet_trips": 0}
                        for _wi in (1, 2):
                            _t_sel = _t.time()
                            _pred = []
                            for _k in range(_td_nspp):
                                _bx, _by = _td_Bel(_td_orbit(_k, _td_As))
                                _pred.append(_tdm.predicted_demag(
                                    _dmst.mags, _bx[nst:], _by[nst:], _br_glob, _ar_r,
                                    MU0))
                            _pred = np.array(_pred)            # (frames, magnets)
                            if not np.all(np.isfinite(_pred)):
                                raise RuntimeError("TDM demag shortcut: non-finite "
                                                   "predicted demag")
                            _kw, _mw = np.unravel_index(int(np.argmax(_pred)),
                                                        _pred.shape)
                            _k0 = int(_kw) - _W // 2
                            _sel = {"window": _wi, "worst_frame": int(_kw),
                                    "worst_magnet_tag": int(_dmst.mags[_mw]["tag"]),
                                    "predicted_drop": float(_pred[_kw, _mw]),
                                    "window_frames": [int(_k0), int(_k0 + _W - 1)],
                                    "selection_s": _t.time() - _t_sel}
                            if float(_pred.max()) <= 0.0:
                                _sel["skipped"] = "no element reaches the knee on the orbit"
                                _td_dm["windows"].append(_sel)
                                break
                            _b0w = _br_glob.copy()
                            _mres = _tdm.demag_march(
                                list(range(_k0, _k0 + _W)),
                                (_td_orbit(_k0 - 1, _td_As), _td_orbit(_k0 - 2, _td_As)),
                                _td_orbit_start, _td_solve_frame, _td_ratchet, log=log)
                            _new, _mapi = _tdm.map_br_from_reference(
                                _dmst.mags, int(_mw), _br_glob, _td_maps)
                            if not _mapi["complete"]:
                                raise RuntimeError(
                                    "TDM demag shortcut: the pole map left %d of %d "
                                    "magnet elements unmapped"
                                    % (_mapi["of"] - _mapi["mapped"], _mapi["of"]))
                            _br_glob[:] = np.minimum(_br_glob, _new)
                            if np.any(_br_glob > _b0w + 1e-12):
                                raise RuntimeError("TDM demag shortcut: Br rose "
                                                   "(irreversibility violated)")
                            _td_rebuild_fmag()
                            _n_dmpre += int(_mres["frames"])
                            for _ck in ("frames", "solves", "ratchet_trips"):
                                _td_dm["march"][_ck] += int(_mres[_ck])
                            _td_resolve_orbit("after demag shortcut window %d" % _wi)
                            _td_mark("demag")
                            _ow = _td_observe(_td_As, _td_Us, _td_orbit(-1, _td_As),
                                              _td_orbit(-2, _td_As), list(range(_td_N)))
                            _rec_w = dict(_dm_change(_b0w, _br_glob),
                                          frames=int(_mres["frames"]), map=_mapi,
                                          selection=_sel, monotone=True,
                                          torque={"T_mean": float(_ow["T_mean"]),
                                                  "ripple_pct": float(_ow["ripple_pct"]),
                                                  "torque_method": _ow["torque_method"]})
                            _td_dm["windows"].append(_rec_w)
                            _dm_passes.append(_rec_w)
                    if not (_td_dmode == "shortcut" and _td_maps is not None):
                        # THE FIXED POINT (shared rule, `_dm_settle_tol`): the
                        # period BEFORE the reported window, θ < 0 — the march's
                        # own pre-pass rotor positions — repeated, each repeat the
                        # NEXT period in time (Br relabelled one period back, the
                        # orbit re-solved on it), until a whole period moves Br by
                        # no more than the tolerance
                        _td_dm["passes"] = []
                        _td_cap = _dm_cap()
                        for _pp in range(_td_cap):
                            if _pp > 0:
                                _br_glob[:] = _dm_relabel(_br_glob)
                                _td_rebuild_fmag()
                                _td_resolve_orbit("before pre-pass period %d" % (_pp + 1))
                                _td_mark("demag")
                            _b0 = _br_glob.copy()
                            _dm_tq_cur.clear()
                            _mres = _tdm.demag_march(
                                list(range(-_td_nspp, 0)),
                                (_td_orbit(-_td_nspp - 1, _td_As),
                                 _td_orbit(-_td_nspp - 2, _td_As)),
                                _td_orbit_start, _td_solve_frame, _td_ratchet, log=log)
                            _n_dmpre += int(_mres["frames"])
                            _chg = _dm_change(_b0, _br_glob)
                            _td_dm["passes"].append(dict(_mres, **_chg))
                            _rec_p = dict(_chg, frames=int(_mres["frames"]),
                                          torque=_dm_take_torque())
                            _dm_passes.append(_rec_p)
                            if _dm_pass_ok(_rec_p, _dm_passes):
                                break
                            log.info("TDM demag pre-pass period %d moved Br by %.3g "
                                     "(worst magnet, area mean; tol %.1g), %.3g at "
                                     "one element (tol %.1g); image history %s; "
                                     "drift %s%s", _pp + 1,
                                     _chg["per_magnet_mean_max"], _dm_settle_tol,
                                     _chg["element_max"], _dm_settle_etol,
                                     _rec_p.get("image_history"), _rec_p.get("drift"),
                                     " — one more period" if _pp + 1 < _td_cap
                                     else " — cap reached")
                        _td_dm["march"] = {
                            _kk: sum(int(_p_[_kk]) for _p_ in _td_dm["passes"])
                            for _kk in ("frames", "solves", "ratchet_trips")}
                        if _td_maps is not None:
                            # diagnostic: the element-wise minimum over the pole
                            # images (every magnet eventually sees what its images
                            # saw)
                            _br_asym = _tdm.image_min_br(_dmst.mags, _br_glob, _td_maps)
                            _mi_all = np.concatenate([np.asarray(_d["idx"], int)
                                                      for _d in _dmst.mags])
                            _td_dm["image_min_gap"] = {
                                "max": float(np.max(_br_glob[_mi_all] - _br_asym[_mi_all])),
                                "mean_area": float(np.sum((_br_glob[_mi_all]
                                                           - _br_asym[_mi_all])
                                                          * _ar_r[_mi_all])
                                                   / max(np.sum(_ar_r[_mi_all]), 1e-30))}
                    _mi_all = np.concatenate([np.asarray(_d["idx"], int)
                                              for _d in _dmst.mags])
                    _td_dm["br_kept_area_pct"] = float(
                        100.0 * np.sum(_br_glob[_mi_all] * _ar_r[_mi_all])
                        / max(np.sum(_br_pristine[_mi_all] * _ar_r[_mi_all]), 1e-30))
                    _td_dm["br_min"] = float(np.min(_br_glob[_mi_all]))
                    _td_dm["br_map"] = [float("%.6g" % _v) for _v in _br_glob[_mi_all]]
                    _tdm_info["t"]["demag"] = (_t.time() - _t_s
                                               - _tdm_info["t"].get("resolve", 0.0))
                    # the orbit on the final magnet
                    _td_resolve_orbit("final")
                    _tdm_info["resolve"] = _td_rs
                    _tdm_info["demag"] = _td_dm
                elif demag and _dm_seeded:
                    _tdm_info["demag"] = {"mode": "seeded (sweep mode): no pre-pass"}
            except BaseException:
                _td_solver.close()
                raise
            try:
                # ── THE CLOSURE MARCH (second Codex review, 2026-10-03, finding 1) ──
                # The Newton may stop on the owner's terms at a state residual
                # < 1e-5, which by itself bounds nothing.  So the orbit is PROVEN:
                # one whole electrical period is marched from the orbit's start
                # state — BOTH BDF2 history levels (A_-1, A_-2) — with the march's
                # own bordered Newton (P2Drive.eddy_solve, the frame loop's solve)
                # and the Br map frozen at the orbit's (the closure is a property of
                # the orbit, not of the ratchet).  For a half-period orbit the march
                # covers BOTH halves.  Accepted only if, per conductor group, both
                # history levels at the end of the period are within
                # CLOSURE_GATE["state_rel"] of the orbit's (relative sigma-norm)
                # and the marched window reproduces the orbit's mean torque, ripple
                # and every group's loss within CLOSURE_GATE (owner-tied, see
                # time_periodic).  A failure (or any error computing it) rejects
                # the attempt: the wrapper retries once with the strict 1e-7 stop
                # on the full period, then marches.  Costs one period of frame
                # solves (tdm.t.closure).
                _td_mark("closure", "TDM closure check")
                _t_s = _t.time()
                _td_dte_c = _tdm.DTE_FACTOR * float(dt)
                _td_ncl = int(_td_nspp)            # one whole electrical period
                _td_a1 = _td_orbit(-1, _td_As)
                _td_a2 = _td_orbit(-2, _td_As)
                _td_cA: List[np.ndarray] = []
                _td_cU: List[np.ndarray] = []
                _td_cnit = []
                _td_Ucur = np.array(_td_Us[0], float)
                for _kc in range(_td_ncl):
                    _cancel_point("TDM closure march")
                    _Pc, _frc, _Ivc, _Isc, _mc = _td_ops(_kc)
                    _pdc = np.asarray(_Pc.multiply(_Pc).sum(axis=0)).ravel()
                    _Astc = _Pc @ (np.asarray(_Pc.T @ _td_orbit(_kc, _td_As)).ravel()
                                   / np.maximum(_pdc, 1.0))
                    _Ahc = _tdm.C_M1 * _td_a1 + _tdm.C_M2 * _td_a2
                    _okc, _Ac, _Uc, _rc, _nc = _drv.eddy_solve(
                        _Pc, _frc, _Astc, _td_Ucur, _Ivc, _Ahc,
                        (None if _sat2 else nu_base2),
                        max(int(nonlinear_iterations), 20), dte=_td_dte_c)
                    if not _okc:
                        raise RuntimeError("TDM closure march: bordered Newton did not "
                                           "converge at frame %d (rrel %.2e)" % (_kc, _rc))
                    _td_cA.append(np.asarray(_Ac, float))
                    _td_cU.append(np.asarray(_Uc, float))
                    _td_cnit.append(int(_nc))
                    _td_Ucur = np.asarray(_Uc, float)
                    _td_a2, _td_a1 = _td_a1, _td_cA[-1]
                _tdm_info["t"]["closure"] = _t.time() - _t_s
                # the state: both BDF2 history levels at the end of every orbit period
                # inside the marched one (the half orbit: after each half), per group
                _td_spairs = []
                for _ke in range(_td_N, _td_ncl + 1, _td_N):
                    for _kl in (_ke - 1, _ke - 2):
                        _td_spairs.append(("frame %d" % _kl, _td_cA[_kl],
                                           _td_orbit(_kl, _td_As)))
                _ok_cs, _cl_state = _tdm.state_closure(
                    _td_spairs, _Msig_grp, _tdm.CLOSURE_GATE["state_rel"])
                # the observables: every orbit-period window of the marched period
                # against the orbit's own
                _td_oobs = _td_observe(_td_As, _td_Us, _td_orbit(-1, _td_As),
                                       _td_orbit(-2, _td_As), list(range(_td_N)))
                _cl_win = {}
                _ok_cw = True
                for _wi, _k0 in enumerate(range(0, _td_ncl, _td_N)):
                    _ks = list(range(_k0, _k0 + _td_N))
                    _h1 = _td_cA[_k0 - 1] if _k0 >= 1 else _td_orbit(-1, _td_As)
                    _h2 = _td_cA[_k0 - 2] if _k0 >= 2 else _td_orbit(_k0 - 2, _td_As)
                    _wobs = _td_observe([_td_cA[_k] for _k in _ks],
                                        [_td_cU[_k] for _k in _ks], _h1, _h2, _ks)
                    _okw, _cmpw = _tdm.window_gate(
                        _td_oobs, _wobs,
                        **{_kk: _tdm.CLOSURE_GATE[_kk]
                           for _kk in ("T_rel", "ripple_pp", "P_rel", "P_floor_rel")})
                    _cl_win["frames %d-%d" % (_ks[0], _ks[-1])] = _cmpw
                    _ok_cw = _ok_cw and _okw
                _td_closure = {"ok": bool(_ok_cs and _ok_cw), "frames": int(_td_ncl),
                               "frozen_br": True, "state": _cl_state,
                               "windows": _cl_win,
                               "newton_iterations_mean": float(np.mean(_td_cnit)),
                               "thresholds": dict(_tdm.CLOSURE_GATE)}
                if _td_fault == "closure_gate":
                    _td_closure["ok"] = False
                    _td_closure["injected"] = True
                _tdm_info["closure"] = _td_closure
                # the one-ORBIT-period defect as a wrap-state vector (the space
                # the linearised period map T acts on): both BDF2 history levels
                # after one orbit period, carried back by the period map
                _td_dwrap = np.concatenate([
                    np.asarray(_td_wrap(_td_cA[_td_N - 2]
                                        - _td_orbit(_td_N - 2, _td_As)))[_td_cond],
                    np.asarray(_td_wrap(_td_cA[_td_N - 1]
                                        - _td_orbit(_td_N - 1, _td_As)))[_td_cond]])
                log.info("TDM closure march (%d frames, frozen Br): %s — state worst "
                         "%.2g at %s; windows %s", _td_ncl,
                         "PASSED" if _td_closure["ok"] else "FAILED",
                         _cl_state["worst"], _cl_state["worst_at"],
                         {_wn: "T %.2g, ripple %.2g pp" % (_w["T_mean_rel"], _w["ripple_pp"])
                          for _wn, _w in _cl_win.items()})
                if not _td_closure["ok"]:
                    raise TdmAttemptFailed(
                        "closure",
                        "the one-period march from the orbit does not close on it "
                        "(state worst %.3g at %s, tol %.0e; windows %s)" % (
                            _cl_state["worst"], _cl_state["worst_at"],
                            _tdm.CLOSURE_GATE["state_rel"],
                            {_wn: "T %.2g, ripple %.2g pp, P %s" % (
                                _w["T_mean_rel"], _w["ripple_pp"],
                                ",".join("%s %.2g" % (_g, _v["rel"])
                                         for _g, _v in _w["P"].items() if not _v["ok"]))
                             for _wn, _w in _cl_win.items()}),
                        info=_tdm_info,
                        retry_residual=bool(_td_stop != "residual"),
                        retry_full=bool(_td_neg))
                # ── ORBIT-ERROR ESTIMATE (third and fourth Codex reviews, 2026-10-04)
                # A FIRST-ORDER ESTIMATE WITH AN EMPIRICAL SAFEGUARD — not a
                # proof (the owner stopped the proof loop at this level, 2026-10-04).
                # The closure measures the one-period DEFECT d; the orbit error is
                # e = (I - T)^-1 d to first order, amplified by 1 / (1 - lambda) on
                # a slow mode.  So: T at the final orbit, its dominant eigenvalue
                # rho_ritz by Arnoldi from d (with a convergence check), e by the
                # preconditioned GMRES — and, as the SAFEGUARD, the contraction
                # OBSERVED on the closure march itself: when the Ritz value has
                # not converged or rho_eff = max(rho_ritz, rho_observed) >= 0.98,
                # further closure periods are marched (at most EXTRA_CLOSURE_MAX)
                # and the decay of the march's deviation from the orbit, period
                # by period, gives rho_observed and an observed orbit error
                # |D_p| + |D_p - D_(p-1)| rho_eff / (1 - rho_eff).  The error used
                # is the largest of |e|, |d| / (1 - rho_eff) and the observed one
                # (on e's direction).  It is propagated through the period and the
                # observables are evaluated on the perturbed orbit — torque mean,
                # ripple, every conductor group's loss — and the IRON LOSS directly
                # with the report's functional (`losses.iron_loss_series`) on the
                # perturbed orbit and on the closure-march period.  The verdict
                # (`estimate_check`) is taken after the frame loop, where the
                # total loss is known.
                _td_mark("error_estimate", "TDM orbit-error estimate")
                _t_s = _t.time()
                _td_solver.refresh_jacobian(_td_As, _td_Us)
                _ctr = _td_solver.contraction(_td_dwrap, m=15)
                _rho_r = float(_ctr["rho"])
                _e_w, _e_info = _td_solver.orbit_error(_td_dwrap)
                _d_n = float(np.linalg.norm(_td_dwrap))
                _e_n = float(np.linalg.norm(_e_w))
                _a_n = float(np.linalg.norm(np.concatenate([
                    np.asarray(_td_As[_td_N - 2])[_td_cond],
                    np.asarray(_td_As[_td_N - 1])[_td_cond]])))

                def _td_dev(kend):
                    """The march's deviation from the orbit at the end of the
                    orbit period ending at frame ``kend`` (both history levels),
                    carried back to the first period's frame."""
                    _pp = (kend + 1) // _td_N
                    _v2 = _td_cA[kend - 1] - _td_orbit(kend - 1, _td_As)
                    _v1 = _td_cA[kend] - _td_orbit(kend, _td_As)
                    for _ in range(_pp):
                        _v2 = _td_wrap(_v2)
                        _v1 = _td_wrap(_v1)
                    return np.concatenate([np.asarray(_v2)[_td_cond],
                                           np.asarray(_v1)[_td_cond]])
                _devs = [_td_dev(_td_N - 1 + _p * _td_N)
                         for _p in range(_td_ncl // _td_N)]
                _incs = [float(np.linalg.norm(_devs[0]))] + [
                    float(np.linalg.norm(_devs[_i] - _devs[_i - 1]))
                    for _i in range(1, len(_devs))]

                def _rho_observed():
                    _r = [(_incs[_i] / _incs[_i - 1]) for _i in range(1, len(_incs))
                          if _incs[_i - 1] > 0.0]
                    return (max(_r[-2:]) if _r else None)
                _rho_o = _rho_observed()
                _rho_eff = max(_rho_r, _rho_o or 0.0)
                _extra = 0

                def _need_more():
                    if _incs[-1] <= 1e-2 * _tdm.CLOSURE_GATE["state_rel"] * _a_n:
                        return False           # the march itself has stopped moving
                    return (_rho_eff >= _tdm.RHO_SAFEGUARD
                            or (not _ctr["ritz_converged"] and _rho_o is None))
                while _need_more() and _extra < _tdm.EXTRA_CLOSURE_MAX:
                    # one more orbit period of the closure march (frozen Br),
                    # continuous: the history is the march's own last two frames
                    _k_lo = len(_td_cA)
                    _td_a1, _td_a2 = _td_cA[-1], _td_cA[-2]
                    for _kc in range(_k_lo, _k_lo + _td_N):
                        _cancel_point("TDM closure march (safeguard)")
                        _Pc, _frc, _Ivc, _Isc, _mc = _td_ops(_kc)
                        _pdc = np.asarray(_Pc.multiply(_Pc).sum(axis=0)).ravel()
                        _Astc = _Pc @ (np.asarray(_Pc.T @ _td_cA[-1]).ravel()
                                       / np.maximum(_pdc, 1.0))
                        _Ahc = _tdm.C_M1 * _td_a1 + _tdm.C_M2 * _td_a2
                        _okc, _Ac, _Uc, _rc, _nc = _drv.eddy_solve(
                            _Pc, _frc, _Astc, _td_cU[-1], _Ivc, _Ahc,
                            (None if _sat2 else nu_base2),
                            max(int(nonlinear_iterations), 20), dte=_td_dte_c)
                        if not _okc:
                            raise RuntimeError(
                                "TDM closure march (safeguard): bordered Newton did "
                                "not converge at frame %d (rrel %.2e)" % (_kc, _rc))
                        _td_cA.append(np.asarray(_Ac, float))
                        _td_cU.append(np.asarray(_Uc, float))
                        _td_a2, _td_a1 = _td_a1, _td_cA[-1]
                    _extra += 1
                    _devs.append(_td_dev(len(_td_cA) - 1))
                    _incs.append(float(np.linalg.norm(_devs[-1] - _devs[-2])))
                    _rho_o = _rho_observed()
                    _rho_eff = max(_rho_r, _rho_o or 0.0)
                _est: Dict[str, Any] = {
                    "kind": "first-order estimate with an empirical safeguard "
                            "(not a proof)",
                    "rho_ritz": _rho_r, "ritz_top": _ctr["ritz_top"],
                    "ritz_history": _ctr["ritz_history"],
                    "ritz_converged": _ctr["ritz_converged"],
                    "arnoldi_steps": _ctr["arnoldi_steps"],
                    "rho_observed": _rho_o, "rho_eff": _rho_eff,
                    "closure_periods": len(_devs), "extra_closure_periods": _extra,
                    "deviation_increments": [float("%.4g" % _x) for _x in _incs],
                    "defect_norm": _d_n, "orbit_state_norm": _a_n,
                    "orbit_error_norm": _e_n, "orbit_error_gmres": _e_info,
                    "safety": _tdm.ESTIMATE_SAFETY}
                _unresolved = (_rho_eff >= _tdm.RHO_SAFEGUARD and _need_more())
                if (not (math.isfinite(_rho_eff) and _rho_eff < 1.0)
                        or not _e_info["converged"] or _unresolved):
                    _est.update({"scale": float("inf"), "dT_rel": float("inf"),
                                 "dripple_pp": float("inf"), "dP_cond_W": float("inf"),
                                 "dP_fe_W": float("inf"),
                                 "why": ("the slow mode is not resolved after %d extra "
                                         "closure period(s) (rho_eff %.4g)"
                                         % (_extra, _rho_eff)) if _unresolved
                                 else ("rho_eff >= 1" if not (math.isfinite(_rho_eff)
                                                              and _rho_eff < 1.0)
                                       else "orbit-error GMRES did not converge")})
                else:
                    _err_rho = _d_n / (1.0 - _rho_eff)
                    _err_obs = ((float(np.linalg.norm(_devs[-1]))
                                 + _incs[-1] * _rho_eff / (1.0 - _rho_eff))
                                if len(_devs) >= 2 else 0.0)
                    _err = max(_e_n, _err_rho, _err_obs)
                    _scl = (_err / _e_n if _e_n > 0.0 else 1.0)
                    _est.update({"error_norm_rho": _err_rho, "error_norm_observed":
                                 (_err_obs if len(_devs) >= 2 else None),
                                 "error_norm": _err, "scale": _scl,
                                 "amplification": (_err / _d_n if _d_n > 0.0 else 1.0)})
                    _w_c = _scl * _e_w
                    _dA_c, _dU_c = _td_solver.propagate(_w_c)
                    _ncd = int(_td_cond.size)

                    def _padc(v):
                        _z = np.zeros(N2)
                        _z[_td_cond] = v
                        return _z
                    _As1 = [_a + _d for _a, _d in zip(_td_As, _dA_c)]
                    _Us1 = [_u + _d for _u, _d in zip(_td_Us, _dU_c)]
                    _obs1 = _td_observe(_As1, _Us1,
                                        _td_orbit(-1, _td_As) + _padc(_w_c[_ncd:]),
                                        _td_orbit(-2, _td_As) + _padc(_w_c[:_ncd]),
                                        list(range(_td_N)))

                    def _td_fe(states):
                        """The iron loss [W, machine] of one electrical period of
                        frames ``states``, with the REPORT's functional
                        (losses.iron_loss_series: stator closed window, rotor
                        open window — the same on every candidate compared)."""
                        _sx, _sy, _rx, _ry = [], [], [], []
                        for _Ak in states:
                            _bxq, _byq, _dxq = _p2_B_at_quad(b2, _Ak)
                            _ar = _dxq.sum(axis=1)
                            _bxe = (_bxq * _dxq).sum(axis=1) / np.maximum(_ar, 1e-30)
                            _bye = (_byq * _dxq).sum(axis=1) / np.maximum(_ar, 1e-30)
                            _sx.append(_bxe[_iron_s_idx]); _sy.append(_bye[_iron_s_idx])
                            _rx.append(_bxe[_iron_r_idx + nst])
                            _ry.append(_bye[_iron_r_idx + nst])
                        _tot = 0.0
                        for _hx, _hy, _ix, _arh, _mt, _cl in (
                                (_sx, _sy, _iron_s_idx, areas_s, _steel_s, True),
                                (_rx, _ry, _iron_r_idx, areas_r, _steel_r, False)):
                            if _mt is None or not np.asarray(_ix).size:
                                continue
                            _pc, _ph = _iron_loss_series(
                                _hx, _hy, _ix, _arh, _mt, p.stack_length, f_elec,
                                len(_hx), _central_difference(dt),
                                _mat_lib.effective_bertotti, terms=None,
                                n_periods=1.0, closed_window=_cl)
                            _tot += (float(np.mean(_pc)) + float(_ph)) * NS
                        return _tot
                    _nper = int(_td_nspp)
                    _orb = [_td_orbit(_k, _td_As) for _k in range(_nper)]
                    _dAfull = [(_dA_c[_k] if _k < _td_N else
                                _td_wrapf(_dA_c[_k - _td_N])) for _k in range(_nper)]
                    _fe0 = _td_fe(_orb)
                    _fe1 = _td_fe([_a + _d for _a, _d in zip(_orb, _dAfull)])
                    _fec = _td_fe(_td_cA[:_nper])
                    _est.update({
                        "ripple_pct": float(_td_oobs["ripple_pct"]),
                        "dT_rel": abs(_obs1["T_mean"] - _td_oobs["T_mean"])
                        / max(abs(_td_oobs["T_mean"]), 1e-300),
                        "dripple_pp": abs(_obs1["ripple_pct"] - _td_oobs["ripple_pct"]),
                        "dP_cond_W": float(sum(abs(_obs1["P"].get(_g, 0.0) - _v)
                                               for _g, _v in _td_oobs["P"].items())),
                        "dP_cond_by_group_W": {_g: abs(_obs1["P"].get(_g, 0.0) - _v)
                                               for _g, _v in _td_oobs["P"].items()},
                        "P_fe_orbit_W": _fe0, "P_fe_perturbed_W": _fe1,
                        "P_fe_closure_W": _fec,
                        "dP_fe_W": abs(_fe1 - _fe0) + abs(_fec - _fe0)})
                    _dA_c = _dU_c = _As1 = _Us1 = _orb = _dAfull = None
                _td_cA = _td_cU = None
                _tdm_info["orbit_error_estimate"] = _est
                _tdm_info["t"]["error_estimate"] = _t.time() - _t_s
                log.info("TDM orbit-error estimate input: rho_ritz %.4g (converged "
                         "%s), rho_observed %s, rho_eff %.4g, %d closure period(s), "
                         "defect %.3g, error %.3g", _rho_r, _ctr["ritz_converged"],
                         _rho_o, _rho_eff, len(_devs), _d_n,
                         float(_est.get("error_norm", float("nan"))))
            finally:
                _td_solver.close()
            # ── hand the orbit to the frame loop: one period, no warm-up ─────────
            _td_mark("splice")
            _tdm_orbit = _td_As
            _tdm_orbit_U = _td_Us
            _tdm_rep = {}          # the reported frames (A, U), for the gate
            _A_m1 = _td_orbit(-1, _td_As)
            _A_m2 = _td_orbit(-2, _td_As)
            _Aed_prev = _A_m1.copy()
            _Aed_prev2 = _A_m2.copy()
            _hed_prev = float(dt)
            _A2_prev = _A_m1.copy()
            _nu_conv2 = nu_base2.copy()
            _Ued = np.array(_td_Us[0], float)

            def _td_net(k):
                _Iv = _td_ops(k)[2]
                return (_Iv.copy(), _Iv.copy())
            _Ib_prev = _td_net(-1)
            _Ib_prev2 = _td_net(-2)
            _Is_m1 = _td_ops(-1)[3]
            _pre_frame = {"psi": tuple(_psi2(_A_m1)),
                          "I": (_Is_m1['A'], _Is_m1['B'], _Is_m1['C'])}
            _tdm_starts = {k: _td_orbit(k, _td_As) for k in range(n_total)}
            _fseq = list(range(n_total))
            _warm_done = True
            _warm_quiet = True
            _warm_resid = float(_tdm_info["solve"].get("rrel_max", 0.0))
            _warm_gauge = {"method": "tdm_time_periodic",
                           "period": _tdm_info["period"],
                           "frames": int(_td_N),
                           "newton_iterations": _tdm_info["solve"].get("newton_iterations"),
                           "rrel_max": _warm_resid}
            _static_seed_info = {"tdm": True}
            if demag and _dmst is not None and _dmst.active:
                _dm_ratchet = True
                _dm_prepass = True
            _tdm_info["t"]["total_before_report"] = _t.time() - _tdm_t0
            _tdm_info["t_report_start"] = _t.time()
            log.info("TDM: orbit solved in %.1f s (%s); marching the reported "
                     "period from it", _tdm_info["t"]["total_before_report"],
                     {k_: (round(v_, 2) if isinstance(v_, float) else v_)
                      for k_, v_ in _tdm_info["t"].items()})
        except TdmAttemptFailed:
            raise                            # a gate's own verdict, with its retry hint
        except Exception as _e_tdm:          # noqa: BLE001 — discarded, marched
            # (a Stop is a BaseException and is not caught here).  NOTHING is
            # repaired in place: this whole solve is abandoned and
            # fem_transient_sliding_band runs a clean march (transactional).
            if _tdm_info is not None:
                _tdm_info["failed"] = "%s: %s" % (type(_e_tdm).__name__, _e_tdm)
                _tdm_info.pop("t_report_start", None)
            raise TdmAttemptFailed(
                _td_stage[0], "%s: %s" % (type(_e_tdm).__name__, _e_tdm),
                info=_tdm_info) from _e_tdm
    # Cross-run warm seed (see _SB_WARM_CACHE above).  The cached frame is the
    # one at electrical angle ≡ −3 steps, i.e. EXACTLY the one-dt-old history
    # the first probe frame (k = −2) wants.  The meshes differ between runs
    # (the geometry moved), so project by nearest dof — the probe frames
    # relax whatever the projection missed, and the settle test decides.
    # A strong change of the operating point (current/speed/γ) invalidates
    # the seed up front; borderline cases are caught by the settle test.  Under
    # SB_SEED_FROM_PREVIOUS (sweeps/optimizer only) the current and γ terms are
    # dropped and the settle test alone decides — see `_warm_seed_accept`.
    # DEMAG runs, INTERACTIVE (flag off): if Br ratchets during the probe (a
    # fresh magnet under load always does), the quiet test refuses the handoff
    # and the run extends — the seed carries no Br state into an interactive
    # run, so it keeps its full pre-ratchet period and its reproducibility.
    # DEMAG runs, SWEEP MODE: the Br map came in with the seed (see the block
    # beside `_dmst`), so the pre-pass is skipped instead.
    _warm_seeded = False   # the eddy history continues the previous run's
    # A seed projected from a DIFFERENT geometry can be a bad Newton start:
    # the sweep of 2026-09-06 (air_gap 1.6 → 2.1 with the live machine's seed)
    # failed 4 of 5 fresh points with "bordered Newton did not converge at
    # frame -2 (6 its, rrel 9e-3)".  A seed is an accelerator, never a
    # requirement, so the first probe frame that fails on a seeded start is
    # re-solved COLD (zero history, no reference gauge) exactly once — the
    # settle test then extends the march as it would on any cold run.
    _seed_cold_retry_done = False
    _wc_A = None          # near-final frame stashed for the NEXT run's seed
    _wc_A2 = None         # …and the one before it (BDF2's second level)
    _wc_Ued = None
    _warm_ref = None      # previous run's per-frame solid loss (same angles)
    _warm_ref_grp: Dict[str, Any] = {}   # …and per conductor group
    if (eddy and not _vdrive and n_total >= 8 and _wc_seed is not None
            and _eddy_method != "tdm"):      # TDM solves the orbit, no seed
        _wc = _wc_seed
        try:
            from scipy.spatial import cKDTree as _KDT
            _widx = _KDT(_wc["doflocs"]).query(
                np.asarray(b2.doflocs.T, dtype=np.float32), k=1)[1]
            _Aed_prev = np.asarray(_wc["A"], float)[_widx]
            # BDF2 needs the level before it too; a seed published by a BDF2
            # run carries it (frame n_total−4), an older one does not → the
            # first step is backward Euler.
            if (_eddy_bdf2 and _wc.get("A2") is not None
                    and len(_wc["A2"]) == len(_wc["A"])):
                _Aed_prev2 = np.asarray(_wc["A2"], float)[_widx]
                _hed_prev = float(dt)
            if len(_wc["Ued"]) == len(_ed_con):
                _Ued = np.asarray(_wc["Ued"], float).copy()
            # SAME-ANGLE REFERENCE — only from a parent at the SAME operating
            # point.  This gauge can only make the handoff test more permissive
            # (it passes the probe when each sample matches the parent's
            # settled loss at that rotor angle), and that is sound only while
            # the parent's settled level IS this run's.  Sweep mode now accepts
            # a seed from any current and γ, where the two levels differ by
            # construction — measured 422 % on the sandbox — so the reference
            # is withheld there and the residual trend gauge decides alone.
            # Extending the guard, not weakening it (user 2026-09-06).
            if (len(_wc.get("solid", ())) == n_total
                    and abs(float(_wc["I"]) - float(I_phase_rms))
                        <= 0.05 * max(abs(float(I_phase_rms)), 1e-9)
                    and abs(float(_wc["gam"]) - float(gamma_deg)) <= 5.0):
                _warm_ref = np.asarray(_wc["solid"], float)
                _wrg = _wc.get("solid_grp") or {}
                _warm_ref_grp = {str(_gk): np.asarray(_gv, float)
                                 for _gk, _gv in _wrg.items()
                                 if len(_gv) == n_total}
            _warm_seeded = True
            log.info("P2 eddy warm cache: eddy history seeded from the "
                     "previous run (%d → %d dofs; parent %.4g A / %.4g deg%s)",
                     len(_wc["A"]), N2, float(_wc["I"]), float(_wc["gam"]),
                     ", sweep mode" if _seed_from_previous() else "")
        except Exception as _wce:   # a bad seed must never fail the run
            _warm_seeded = False
            _Aed_prev2 = None; _hed_prev = None
            log.info("P2 eddy warm cache: seed skipped (%s)", _wce)
    elif eddy and not _vdrive and n_total >= 8:
        log.info("P2 eddy warm cache: COLD start (%s)", _wc_why or "no seed")
    _ed_cu = []; _ed_mag = []; _ed_sh = []   # σ∫E² per frame [W, machine]
    _ed_sl = []                              # …and the retaining sleeve's
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
    # The WARM-UP prefix (θ<0) is a prefix of the SAME march, so its frames must
    # use the first scheduled frame's (dθ, dt) pair.  The old expressions took
    # the schedule AVERAGE step for the angle and the REPORTED window's dt for
    # the clock: identical on a uniform schedule (kept verbatim below so nothing
    # there moves by an ulp), but on the mixed PWM one they spun the rotor ~3x
    # faster than the clock the voltage circuit integrated, which hands frame 0
    # a circuit state off its orbit.  (B5 / PWM study 2026-09-13.)
    _warm_dth = float(_sched_dth[0])
    _warm_dt = float(_sched_dt[0])
    _fseq = list(range(-_eddy_probe, 0)) + list(range(n_total))
    if _eddy_method == "tdm":           # the orbit replaced the warm-up
        _fseq = list(range(n_total))
    # ── COLD START FROM THE STATIC FIELD, NOT FROM ZERO (2026-09-24) ─────────
    # See p2_drive.eddy_static_state: the history handed to the first march
    # frame is the ∂A/∂t = 0 field one step before it, so the rotor-frame DC
    # flux is already in the conducting shaft/magnets (as on the periodic
    # orbit) instead of being switched on in one Δt and left to diffuse — on
    # the L155's steel shaft that diffusion alone would take hundreds of
    # electrical periods.  Seeded runs keep their seed; frozen-ν runs keep the
    # reference-frame semantics; voltage runs already start from the phasor
    # initialiser's operating field.
    if _eddy_method != "tdm":           # a TDM run recorded its own start
        _static_seed_info = None
    # SB_EDDY_ZERO_START=1: the pre-2026-09-24 cold start from A = 0 — for the
    # test that reproduces the start-up transient, never a production knob.
    if (eddy and not _vdrive and not _warm_seeded and not frozen_nu
            and _fseq and _ed_con and _eddy_method != "tdm"
            and _os_sb.environ.get("SB_EDDY_ZERO_START") != "1"):
        try:
            _ks = int(_fseq[0]) - 1
            _ths = (_ks / n_total) * period_mech * n_periods
            _ms_s = int(round(_ths / spacing))
            _the_s = _ms_s * spacing
            _dth_s = period_mech * n_periods / n_total
            _fb_s = _Feedback(k=_ks, theta_prev_deg=_the_s - _dth_s,
                              theta_deg=_the_s, t0_s=float(_ks * dt),
                              t1_s=float((_ks + 1) * dt), fine=True,
                              i_abc=None, psi_abc=None, v_bus=_fb_v_bus)
            _Ist_s = _src.mean_over(_fb_s)
            _Pro_s, _out_s = _proj.build(_ms_s)
            _free_s = np.setdiff1d(np.arange(_Pro_s.shape[1]), _out_s)
            _f_s = (f_mag2 + _Ist_s['A'] * f_coil2['A']
                    + _Ist_s['B'] * f_coil2['B'] + _Ist_s['C'] * f_coil2['C'])
            _nu_s = nu_base2.copy()
            _A_s0 = np.zeros(N2)
            if _sat2:
                _A_s0, _nu_s, _, _ = _p2.pic2_sweeps(
                    _Pro_s, _free_s, np.asarray(_Pro_s.T @ _f_s).ravel()[_free_s],
                    _nu_s, _PIC_SEED_MAX, _PIC_SEED_TOL)
            _I_vec_s = np.array(
                [_Ist_s[c["phase"]] * c["Iunit"] if c["key"] == "cu" else 0.0
                 for c in _ed_con], float)
            _cancel_point("static start field")
            _ok_s, _A_static = _drv.eddy_static_state(
                _Pro_s, _free_s, _A_s0, _I_vec_s,
                (None if _sat2 else nu_base2), max(int(nonlinear_iterations), 20))
            if _ok_s:
                _Aed_prev = _A_static.copy()
                # the ring conductors' history without the snapshot's
                # non-pole-pair-periodic imprint (see _rotor_image_mean);
                # SB_EDDY_START_IMAGE_MEAN=0 is the one-angle start before.
                _img_info = None
                if _os_sb.environ.get("SB_EDDY_START_IMAGE_MEAN", "1") != "0":
                    try:
                        _Aed_prev, _img_info = _rotor_image_mean(_A_static)
                    except Exception as _e_im:   # noqa: BLE001 — a start
                        _Aed_prev = _A_static.copy(); _img_info = None
                        log.warning("P2 eddy warm-up: pole-pair image mean "
                                    "of the start failed (%s) — one-angle "
                                    "static start", _e_im)
                _Aed_prev2 = None; _hed_prev = None   # first step: BE
                _Ib_prev = None; _Ib_prev2 = None
                _A2_prev = _A_static.copy()
                _nu_conv2 = _nu_s.copy()
                _static_seed_info = {"frame": _ks, "converged": True,
                                     "rotor_image_mean": _img_info}
                log.info("P2 eddy warm-up: cold start from the STATIC field at "
                         "frame %d (∂A/∂t = 0 limit), not from A = 0", _ks)
            else:
                _static_seed_info = {"frame": _ks, "converged": False}
                log.warning("P2 eddy warm-up: static start field did not "
                            "converge — starting from A = 0 (longer settle)")
        except Exception as _e_ss:          # noqa: BLE001 — a start, not a result
            _static_seed_info = {"error": "%s: %s" % (type(_e_ss).__name__, _e_ss)}
            log.warning("P2 eddy warm-up: static start failed (%s) — starting "
                        "from A = 0", _e_ss)
    _cancel_point("first time step")
    _fi = 0
    while _fi < len(_fseq):
        k = _fseq[_fi]; _fi += 1
        # Br when the REPORTED window opens (the demag settle measure, both
        # methods): the first reported frame solved with the ratchet active
        if (_dm_rep_br0 is None and demag and _dmst is not None and _dm_ratchet
                and k >= int(_vskip) + int(_dmskip)):
            _dm_rep_br0 = _br_glob.copy()
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
            # The demag pre-pass is spliced in as θ<0 frames too (they are
            # solved and discarded exactly like warm-up frames), so it counts
            # itself here for free — but it gets its OWN phase label, because
            # "warm-up" would misname the one stage where the magnet is
            # deliberately being ratcheted (user's bug, 2026-09-05).
            _warm_n = sum(1 for _x in _fseq if _x < 0)
            _done, _tot, _ph = (
                (_warm_n + k, _warm_n,
                 ("demag pre-pass — one settled period, Br ratchet active"
                  if _dm_prepass else
                  "eddy warm-up (adaptive) — settling the start-up transient"))
                if k < 0 else (k, n_total, None))
            # FOUR arguments, then three, then two.  The composition string
            # ("2×36 settle + 8 pre-roll + 144 reported") has to come from
            # here: the frontend used to derive it by assuming total = 3 ×
            # steps/period, which the mixed-resolution schedule breaks.
            # Callers written against the older signatures (refine_proc, the
            # tests) keep working — each arity is tried in turn.
            for _args in ((_done, _tot, _ph, _progress_comp),
                          (_done, _tot, _ph), (max(k, 0), n_total)):
                try:
                    progress_cb(*_args)
                    break
                except TypeError:
                    continue
                except Exception:
                    break
        # ONE line naming what the reported window opens on (user's bug,
        # 2026-09-05: two identical runs differed because the ratchet had seen
        # the start-up transient).  Emitted at the first REPORTED frame, which
        # is the first moment the post-pre-pass Br state exists.
        if k >= 0 and _dm_prepass and not _dm_pre_logged:
            _dm_pre_logged = True
            try:
                _ar_pre = _triangle_areas(half["r"]["mesh"])[_mag_idx]
                _kept_pre = float(
                    np.sum(_br_glob[_mag_idx] * _ar_pre)
                    / max(np.sum(_ar_pre), 1e-30)) / max(float(magnet_scale), 1e-12)
            except Exception:       # a diagnostic may never fail a run
                _kept_pre = float("nan")
            log.info("P2 demag pre-pass done: %d warm-up frame(s) with the Br "
                     "ratchet FROZEN (start-up transient), then %d pre-pass "
                     "frame(s) with it ACTIVE on the settled field%s; Br kept "
                     "%.2f %% entering the reported window.",
                     _n_warm, _n_dmpre,
                     (" — SKIPPED, this run CONTINUES the previous point's "
                      "magnet (sweep mode, user 2026-09-06)" if _dm_seeded
                      else ""),
                     100.0 * _kept_pre)
        theta = (_sched_th[k] if k >= 0 else
                 (k * _warm_dth if _sched_mixed
                  else (k / n_total) * period_mech * n_periods))
        m_shift = int(round(theta / spacing))
        theta_eff = m_shift * spacing
        # Voltage drive: Crank–Nicolson in ROTOR TIME.  The field only exists
        # at SNAPPED slip-node angles θ_eff, so Δt_k = Δθ_eff/ω and V is
        # sampled at the midpoint of the ACTUAL motion.  Dividing a snapped
        # Δψ by the UNIFORM dt instead modulates dψ/dt by the node-
        # quantisation sawtooth (fake volts ≫ |V−E|) and CN rings undamped
        # at Nyquist → monster harmonic currents.
        # Nominal step of THIS frame.  Per-frame, because the settle can march
        # coarse while the reported window marches fine; identical for every
        # frame on the uniform schedule.
        _dth_frame = (_sched_dth[k] if k >= 0 else
                      (_warm_dth if _sched_mixed
                       else period_mech * n_periods / n_total))
        _dt_frame = _sched_dt[k] if k >= 0 else (_warm_dt if _sched_mixed else dt)
        # ── ASK THE SOURCE ───────────────────────────────────────────────
        # One Feedback per frame, for every source: the step's rotor motion
        # (already SNAPPED to slip nodes — that is the motion the field really
        # made), its clock, whether the real modulator runs on it, and the
        # PREVIOUS converged step's currents and flux linkages.  Those
        # measurements are one step old by construction — this call happens
        # before the step is solved — which is exactly the sampling delay a
        # real controller has, so a closed-loop source is not being handed
        # information its hardware could not have.
        _fb_t0 = (float(_sched_t[k]) if k >= 0
                  else float(k * (_warm_dt if _sched_mixed else dt)))
        _fb = _Feedback(
            k=int(k),
            theta_prev_deg=(theta_eff - _dth_frame if _th_eff_prev is None
                            else float(_th_eff_prev)),
            theta_deg=float(theta_eff),
            t0_s=_fb_t0, t1_s=_fb_t0 + float(_dt_frame),
            fine=bool(_sched_fine[k] if k >= 0 else True),
            i_abc=_fb_i_prev, psi_abc=_fb_psi_prev, v_bus=_fb_v_bus)
        if _vdrive:
            if _th_eff_prev is None:        # very first frame: nominal step
                _th_eff_prev = theta_eff - _dth_frame
            _dth_eff = theta_eff - _th_eff_prev
            _dt_k = (_dt_frame * (_dth_eff / _dth_frame)
                     if _dth_eff > 1e-12 else _dt_frame)
            # The MEAN applied voltage over this step — the volt-seconds the
            # Crank–Nicolson residual integrates, and whatever the source says
            # they are.  Under PWM the settle frames (fb.fine False) see the
            # plain sinusoid FUNDAMENTAL — the same amplitude and angle the
            # modulator is compensated to apply — so the coarse dt is only ever
            # asked to resolve a sinusoid, while the fine frames (pre-roll +
            # reported window) see the real chopped inverter.  The changeover
            # is legal because the CN circuit integrates the EXACT volt-seconds
            # of each step and rebuilds its eddy history operator from dt_k
            # every frame (p2_drive.ve_newton: Msd_k = Msig/dt_k, Sdt_k =
            # S_raw·dt_k), so dt is already a per-step quantity rather than a
            # run constant.
            _Vt = _src.mean_over(_fb)
            # WHAT WAS APPLIED, per solved step.  Not the carrier-resolution
            # waveform — the value the circuit actually integrated over this
            # step, which is the honest thing to plot beside the torque and the
            # current: ripple on those lines up with these edges by
            # construction.  Three floats per frame, trimmed with every other
            # per-frame series when the settling prefix is dropped.
            _vapp['A'].append(_Vt['A'])
            _vapp['B'].append(_Vt['B'])
            _vapp['C'].append(_Vt['C'])
            _th_eff_prev = theta_eff
            # DC-orbit solve: the CN flux state this whole period starts from —
            # AFTER any correction applied at the end of the previous one.
            if _dc_orbit is not None and k in _dc_starts:
                _dc_orbit.period_start(_ll_flux(_psi_prev))
            _iv_prev = dict(_iv_state)      # i_{k−1} for the R/2 term
            Ist = dict(_iv_state)           # warm start for the coupled solve
        else:
            _Vt = None; _iv_prev = None
            # Imposed current: a CONSTRAINT at this frame's rotor position, so
            # the source's step mean is its value there.
            Ist = _src.mean_over(_fb)
            _th_eff_prev = theta_eff
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
        if _tdm_starts is not None and k in _tdm_starts:
            # eddy_method="tdm": this frame's own orbit state is the Newton
            # start (on the orbit it IS the answer; the frame's own residual
            # decides, exactly as for any start)
            _pd = np.asarray(Pro.multiply(Pro).sum(axis=0)).ravel()
            _A_start = Pro @ (np.asarray(Pro.T @ _tdm_starts[k]).ravel()
                              / np.maximum(_pd, 1.0))

        # Demag makes the frame re-enterable: solve, check the magnet, and
        # if it weakened, rebuild its source and solve again.  The de-rating
        # is monotone and self-arresting — a weaker magnet makes a weaker
        # demagnetising field — so this settles in a few passes; the cap is a
        # backstop, not a schedule.
        # The eddy step of THIS frame: the rotor-time Δt_k under voltage drive
        # (see p2_drive.ve_newton), the nominal dt otherwise — and the history
        # it is taken against (backward Euler or BDF2, see _eddy_bdf2).
        _h_ed = float(_dt_k if _vdrive else dt)

        def _ed_history():
            """(A_hist, Δt_eff or None, scheme) for this frame's eddy step."""
            if (_eddy_bdf2 and _Aed_prev2 is not None and _hed_prev
                    and 0.0 < _h_ed / _hed_prev <= _BDF2_MAX_RATIO):
                _dte_b, _Ah_b = _P2Drive.bdf2_history(
                    _h_ed, _hed_prev, _Aed_prev, _Aed_prev2)
                return _Ah_b, _dte_b, "bdf2"
            # backward Euler: current drive keeps the construction operators
            # (dte None → the identical objects), voltage drive its Δt_k
            return _Aed_prev, (_h_ed if _vdrive else None), "be"
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
                _Ahist, _dte, _ts_k = _ed_history()
                (_eok, A2, _Ued, _res, _nit) = _drv.eddy_solve(
                    Pro, _free2, _A_start, _Ued, _I_vec, _Ahist,
                    _nu_fix, max(int(nonlinear_iterations), 20), dte=_dte)
                if not _eok and k < 0 and _warm_seeded and not _seed_cold_retry_done:
                    # The seeded start (projected from another geometry /
                    # operating point) did not converge on a WARM-UP frame:
                    # drop the seed and re-solve this frame from a cold history.
                    # Same physics, just a different starting guess — the
                    # reported window is untouched (see the note at the flag).
                    log.warning("P2 eddy warm cache: seeded start did not "
                                "converge at frame %d (%d its, rrel=%.2e) — "
                                "restarting the warm-up COLD", k, _nit, _res)
                    _seed_cold_retry_done = True
                    _warm_seeded = False
                    _warm_ref = None
                    _warm_ref_grp = {}
                    _Aed_prev = np.zeros(N2)
                    _Aed_prev2 = None; _hed_prev = None; _Ib_prev = None; _Ib_prev2 = None
                    _Ued = np.zeros(len(_ed_con))
                    continue
                if not _eok:
                    # No silent fallback: the magnetostatic Picard below would
                    # solve DIFFERENT physics (no σ·∂A/∂t, coil current back as
                    # a source) and report it as an eddy run.
                    raise RuntimeError(
                        f"P2 coupled eddy: bordered Newton did not converge at "
                        f"frame {k} ({_nit} its, rrel={_res:.2e})")
                _newton_ok = True
                if _ed_paths is not None and k in (0, n_total - 1):
                    # What the solder joint actually does, in amperes, on the
                    # first and last reported frame: the strand currents and
                    # how far they sit from the equal split a transposed coil
                    # would have.  Reported rather than trusted — this is the
                    # whole quantity the run exists to measure.
                    _pc = getattr(_drv, "path_share", None)
                    if _pc is not None:
                        log.info("P2 series paths, frame %d: strand currents "
                                 "%s A; worst departure from the equal split "
                                 "%.1f A (%.1f %% of the coil's %.1f A)",
                                 k, np.array2string(_pc[:8], precision=1),
                                 _drv.path_circ,
                                 100.0 * _drv.path_circ
                                 / max(abs(float(np.sum(_pc[:int(wire_parallel)]))),
                                       1e-30),
                                 abs(float(np.sum(_pc[:int(wire_parallel)]))))
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
                _Ahist, _dte, _ts_k = _ed_history()
                (_eok, A2, _Ued, _viA, _viB, _res, _nit,
                 _vrc) = _drv.ve_newton(
                    Pro, _free2, _A_start, _Ued, (Ist['A'], Ist['B']),
                    _Ahist, _Vt, _dt_k, _iv_prev, _psi_prev,
                    _nu_fix, max(int(nonlinear_iterations), 25), dte=_dte)
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
                        _du = _p2.solve_ff(Jff, -r_free, spd=True)
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
            # `_dm_ratchet` is False for exactly the frames of the eddy warm-up
            # march (see the freeze block above): a monotone irreversible rule
            # may not be shown the σ·∂A/∂t START-UP transient, because what it
            # burns in there is not a field the machine was ever in — and it is
            # a different non-field on a cold run than on a warm-cache-seeded
            # one, which is the whole of the user's 2026-09-05 report.  The
            # frames are still SOLVED (the eddy history has to settle); only the
            # magnet is held pristine until the dedicated pre-pass below.
            if _dmst is not None and _dm_ratchet:
                _bxq_d, _byq_d, _dxq_d = _p2_B_at_quad(b2, A2)
                _ar_d = _dxq_d.sum(axis=1)
                _bxe_d = (_bxq_d * _dxq_d).sum(axis=1) / np.maximum(_ar_d, 1e-30)
                _bye_d = (_byq_d * _dxq_d).sum(axis=1) / np.maximum(_ar_d, 1e-30)
                _dm_hit = _dmst.update(_bxe_d[nst:], _bye_d[nst:])
                if _dm_hit and not _warm_done:
                    _dm_moved_in_warm = True     # the quiet test must see this
                if _dm_hit and _dm_pass < 11:
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
                    # ── WHERE the re-solve happens is a question of whose
                    # numbers the frame feeds.  A WARM-UP frame's numbers are
                    # DISCARDED — re-solving it buys nothing the reported
                    # window ever sees — so there the weakening simply carries
                    # into the next frame, exactly the Maxwell convention the
                    # user described (and judging on the pre-weakening field is
                    # the conservative side: the stronger magnet drives the
                    # stronger demagnetising field).  A REPORTED frame's
                    # numbers ARE the result, so a trip there still re-solves:
                    # torque and losses may not be billed on a magnet that no
                    # longer exists.  After a settled warm-up such trips are
                    # rare, so the honest path costs almost nothing.
                    if not _warm_done:
                        break                    # carry into the NEXT frame
                    # a re-solve is a whole Newton; up to 11 per frame
                    _cancel_point("demag re-solve of frame %d" % k)
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
            # …and the SAME derivative the step solved: backward Euler
            # (A_k − A_{k−1})/Δt, or BDF2 (A_k − A_hist)/Δt_eff.
            _dt_e = (_dte if _dte is not None
                     else (_dt_k if _vdrive else dt))
            _dAe = (A2 - _Ahist) * (1.0 / _dt_e)          # ∂A/∂t [V/m]
            if _ts_k == "bdf2":
                _ed_ts["bdf2_steps"] += 1
                _w_k = _h_ed / _hed_prev
                _ed_ts["ratio_min"] = (_w_k if _ed_ts["ratio_min"] is None
                                       else min(_ed_ts["ratio_min"], _w_k))
                _ed_ts["ratio_max"] = (_w_k if _ed_ts["ratio_max"] is None
                                       else max(_ed_ts["ratio_max"], _w_k))
            else:
                _ed_ts["be_steps"] += 1
            # net current of every constrained body at t_k, from its own row
            # (S_b·U_b − g_b·∂A/∂t — imposed, path or ∫J = 0 alike)
            # …and the imposed (transposed) wire currents the 2-D DC
            # reference below bills
            _Ib_k = (_S_con * np.asarray(_Ued, float)
                     - np.asarray(_G2.T @ _dAe).ravel(),
                     np.array([Ist[c["phase"]] * c["Iunit"]
                               if c["key"] == "cu" else 0.0
                               for c in _ed_con], float))
            _Uev = _Ued                  # the U the loss is evaluated with
            _Idc_e = _Ib_k[1]            # the currents the DC reference bills
            if _ed_loss_mid and _ts_k == "bdf2" and _Ib_prev is not None:
                _dAe = (A2 - _Aed_prev) * (1.0 / _h_ed)   # centred at t_{k−½}
                # the net currents AT t_{k−½}: quadratic through the last three
                # levels (3/8, 6/8, −1/8 on a uniform step — third order; the
                # two-point mean would read I² low by cos²(ωΔt/2), −6.7 % at
                # 12 steps), the two-point mean only on the first step
                if _Ib_prev2 is not None and _hed_prev:
                    _Imid = [None, None]
                    for _jq in (0, 1):
                        # Lagrange weights at t = t_k − h/2 through
                        # t_k, t_{k−1} = t_k − h, t_{k−2} = t_k − h − h_prev
                        _h1 = _h_ed; _h2 = _h_ed + _hed_prev; _x = 0.5 * _h_ed
                        _l0 = (_x - _h1) * (_x - _h2) / (_h1 * _h2)
                        _l1 = _x * (_x - _h2) / (_h1 * (_h1 - _h2))
                        _l2 = _x * (_x - _h1) / (_h2 * (_h2 - _h1))
                        _Imid[_jq] = (_l0 * _Ib_k[_jq] + _l1 * _Ib_prev[_jq]
                                      + _l2 * _Ib_prev2[_jq])
                else:
                    _Imid = [0.5 * (_Ib_k[0] + _Ib_prev[0]),
                             0.5 * (_Ib_k[1] + _Ib_prev[1])]
                _Uev = ((_Imid[0] + np.asarray(_G2.T @ _dAe).ravel())
                        / np.maximum(_S_con, 1e-300))
                _Idc_e = _Imid[1]
                _ed_ts["loss_midpoint_steps"] = _ed_ts.get(
                    "loss_midpoint_steps", 0) + 1
            _pg = {_kk: float(_dAe @ (_Mg @ _dAe))
                   for _kk, _Mg in _Msig_grp.items()}
            for _ci, _c in enumerate(_ed_con):
                _u = float(_Uev[_ci])
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
            if _ve_warm and not _warm_done:
                # COMBINED MODE: the voltage settling prefix IS the warm-up.
                # Every prefix frame counts (they are all solved and all
                # discarded); only the gauge-fineness ones are SAMPLED, so the
                # block means the tail fit is built from share one Δt and one
                # rotor spacing.  No extension branch: the length is the
                # schedule's, so an unsettled handoff is REPORTED, never marched
                # away — and the run cannot run away either.
                _n_warm += 1
                if bool(_sched_fine[k]) == _ve_gauge_fine:
                    _warm_solid.append(
                        (_pg.get("mag", 0.0) + _pg.get("shaft", 0.0)) * _wsc)
                    _warm_ks.append(int(k))
                    for _gk, _gv in _pg.items():
                        _warm_grp.setdefault(_gk, []).append(float(_gv) * _wsc)
                if k >= _vskip - 1:
                    _warm_done = True
                    # WHOLE-PERIOD means per group when the prefix holds two
                    # or more periods at the gauge resolution (every carrier
                    # of a synchronous PWM cancels inside a period, so no
                    # block-width caveat applies); the block gauge only for a
                    # prefix too short for that.
                    _ve_per = int(_c_nspp if _sched_mixed else _v_nspp)
                    _pres, _pgres, _pnp = _eddy_period_resid(
                        _warm_grp, _ve_per, machine_extra_W=_warm_cu_W)
                    _ve_period_gauge = bool(_pnp >= 2)
                    if _ve_period_gauge:
                        _warm_resid, _warm_tau_s = float(_pres), None
                        _warm_gauge = {
                            "method": "whole_period_means_per_group",
                            "whole_periods": int(_pnp),
                            "per_group": {_gk: (None if _gr is None
                                                else float("%.3g" % _gr))
                                          for _gk, _gr in _pgres.items()},
                            "extension_periods": 0}
                    elif len(_warm_solid) >= 3:
                        _warm_resid, _warm_tau_s = _eddy_settle_resid(
                            _warm_solid, _ve_gauge_nspp, _ve_gauge_dt)
                        _warm_gauge = {"method": "block_trend_short_prefix",
                                       "whole_periods": int(_pnp)}
                    # The eddy verdict that travels out in the result dict.
                    # No extension path here (the length IS the schedule), so
                    # a False here is by definition "ended at the cap".
                    _wq = bool(_warm_resid is not None
                               and _warm_resid <= _EDDY_SETTLE_TOL)
                    # …EXCEPT where the gauge is MEASURED not to mean what it
                    # says: a chopped drive with fewer than 3 settle periods
                    # has averaging blocks that are not a whole number of
                    # carriers, so the leftover carrier ripple reads as decay.
                    # Measured on ciano14_40_new (16.68 kHz, 240 steps): 2
                    # settle periods -> 8.81 %, 6 -> 8.91 %, with P_mag
                    # identical at 4.678 W.  There is no transient there to
                    # flag — the number is an UPPER BOUND, and the warning
                    # below says so.  Reporting False on that basis would put
                    # an amber "not settled" marker on every PWM run in the
                    # Simulation tab for a residual that does not move when
                    # you triple the warm-up, so the verdict is UNKNOWN
                    # (None → reported settled, i.e. exactly as these runs
                    # have always read) rather than a false alarm.
                    _warm_quiet = (None if (not _wq and _carriers
                                            and not _ve_gauge_carrier_ok
                                            and not _ve_period_gauge)
                                   else _wq)
                    _quiet = (_wq
                              and not (demag and _dm_moved_in_warm))
                    log.info("P2 eddy warm-up (voltage drive): %d settling "
                             "frame(s) solved and discarded, remaining "
                             "start-up transient %s of the settled solid loss "
                             "(tol %.1f %%)%s", _n_warm,
                             "unmeasured" if _warm_resid is None
                             else "%.3g %%" % (100.0 * _warm_resid),
                             100.0 * _EDDY_SETTLE_TOL,
                             "" if _warm_tau_s is None
                             else ", local tail fit tau=%.3g s" % _warm_tau_s)
                    if (not _quiet) and _warm_resid is not None \
                            and _warm_resid > _EDDY_SETTLE_TOL:
                        log.warning(
                            "P2 eddy warm-up (voltage drive): %.3g %% left at "
                            "the handoff after the %d-frame settling prefix "
                            "(tol %.1f %%).  %s",
                            100.0 * _warm_resid, _n_warm,
                            100.0 * _EDDY_SETTLE_TOL,
                            # Under PWM this number is dominated by the CARRIER
                            # ripple the n_steps/6 blocks cannot average away —
                            # measured: tripling the settle moves it 8.81 -> 8.91
                            # %% while P_mag does not move at all.  Telling the
                            # caller to buy more warm-up would be advice that was
                            # measured not to work.
                            "Under PWM with fewer than 3 settle periods this "
                            "gauge cannot separate a decaying transient from "
                            "the switching ripple (its averaging blocks are "
                            "not a whole number of carriers), so treat it as "
                            "an UPPER BOUND on the transient, not as one: on "
                            "the pinned 40 mm case tripling the settle left "
                            "both this number and P_mag unchanged.  With "
                            "SB_V_SETTLE_PERIODS>=3 the gauge becomes "
                            "carrier-commensurate and the number means what "
                            "it says."
                            if (_carriers and not _ve_gauge_carrier_ok) else
                            "P_mag / P_shaft and the efficiency are over-read "
                            "by roughly that much; raise SB_V_SETTLE_PERIODS.")
            elif not _warm_done:
                if (_eec_accum and k < 0 and _warm_extended
                        and _warm_ext_periods in _eec_at):
                    # the unclamped Jacobian of this frame's converged field, for
                    # the period average the DC correction solves with
                    _Kc, _ic = _p2.Kpw(A2)
                    _Tc = _p2.tangent2(_ic, clamp=False) if _ic else None
                    _r_c = getattr(_p2, "last_dhdb_min_rel", None) if _ic else None
                    if _r_c is not None:
                        _eec_dhdb = (_r_c if _eec_dhdb is None
                                     else min(_eec_dhdb, _r_c))
                    _Jc = _Kc if _Tc is None else (_Kc + _Tc)
                    _eec_J = _Jc if _eec_J is None else (_eec_J + _Jc)
                    _eec_nJ += 1
                if k < 0:
                    _n_warm += 1          # every frame at θ<0 is warm-up
                _warm_solid.append(
                    (_pg.get("mag", 0.0) + _pg.get("shaft", 0.0)
                     if ("mag" in _Msig_grp or "shaft" in _Msig_grp)
                     else _pg.get("cu", 0.0)) * _wsc)
                _warm_ks.append(int(k))   # its electrical angle ≡ k mod n_total
                # Per conductor group, continuous across splices (the period
                # remap below keeps the march continuous in time).
                for _gk, _gv in _pg.items():
                    _warm_grp.setdefault(_gk, []).append(float(_gv) * _wsc)
                # Decision point: the probe's third sample IS frame 0; an
                # extension march decides at its own last frame (θ = −dθ),
                # where it already has a whole period of samples.
                if (k == -1) if _warm_extended else (k >= 0):
                    # WHOLE-PERIOD means per group — never three samples: on a
                    # cold probe this is "not yet measurable" (inf) and the
                    # march extends; only a seed's same-angle reference can
                    # pass a probe, because it compares against the settled
                    # level at the same rotor angle.
                    _warm_resid, _warm_gres, _warm_nper = _eddy_period_resid(
                        _warm_grp, int(n_steps_per_period),
                        machine_extra_W=_warm_cu_W)
                    _warm_tau_s = None
                    # Same-angle reference test (warm cache): three probe
                    # samples cannot average away the 6th-harmonic angular
                    # ripple, so the trend gauge above reads ripple as an
                    # unsettled transient and extends almost every march.
                    # But when the eddy state was SEEDED from the previous
                    # run, that run's own measured window says exactly what
                    # the settled solid loss IS at each rotor angle — compare
                    # sample against same-angle reference and the ripple
                    # cancels identically.  A too-different geometry/current
                    # fails this too (its settled level moved) and the march
                    # extends exactly as a cold one would.
                    _ref_dev = None
                    if _warm_ref is not None and not _warm_extended:
                        try:
                            if _warm_ref_grp:
                                # per group, like the period gauge: a slow
                                # shaft must not hide behind the magnets
                                _rtot = sum(float(np.mean(np.abs(_rv)))
                                            for _rv in _warm_ref_grp.values())
                                _ref_dev = max(
                                    abs(_s - _rv[_k2 % n_total])
                                    / max(abs(_rv[_k2 % n_total]),
                                          1e-3 * _rtot, 1e-12)
                                    for _gk2, _rv in _warm_ref_grp.items()
                                    for _s, _k2 in zip(
                                        _warm_grp.get(_gk2, [])[
                                            -len(_warm_ks):], _warm_ks))
                            else:
                                _ref_dev = max(
                                    abs(_s - _warm_ref[_k2 % n_total])
                                    / max(abs(_warm_ref[_k2 % n_total]), 1e-12)
                                    for _s, _k2 in zip(_warm_solid, _warm_ks))
                        except Exception:
                            _ref_dev = None
                    _ref_ok = (_ref_dev is not None
                               and _ref_dev <= _EDDY_SETTLE_TOL)
                    if _ref_dev is not None:
                        log.info("P2 eddy warm cache: probe vs previous run's "
                                 "same-angle frames: max dev %.3g %% (tol "
                                 "%.1f %%) — %s", 100.0 * _ref_dev,
                                 100.0 * _EDDY_SETTLE_TOL,
                                 "settled" if _ref_ok else "not settled")
                    # `_dm_moved_in_warm` can only be True on a path where the
                    # ratchet was NOT frozen through the march; with the freeze
                    # (2026-09-05) the demag settling is the pre-pass's job, not
                    # the warm-up's, so this term simply never fires there.
                    # The EDDY verdict alone (see `_warm_quiet` above): the
                    # trend gauge, or the same-angle reference when a seed
                    # supplied one.  Re-set on every decision, so after an
                    # extension it holds the SECOND test's answer — which is
                    # the one the reported window was actually handed.
                    _warm_quiet = bool(_warm_resid <= _EDDY_SETTLE_TOL
                                       or _ref_ok)
                    # after an accelerator jump: at least MIN_VERIFY_PERIODS
                    # whole continuous periods solved on the new state
                    _acc_check = None
                    if _warm_quiet and any(_j.get("applied") for _j in _acc_jumps):
                        _warm_quiet = bool(_warm_nper >= _acc_min_verify)
                        if _warm_quiet and _acc_last and len(_acc_hist) >= 3:
                            # SLOW-MODE CHECK (in addition to the gauge): the
                            # tail of the slow bodies at the lambda the
                            # accelerator measured on the state, not the
                            # gauge's q <= 0.9 (periodic_accel.slow_tail_resid)
                            _lam_s = None
                            try:
                                _two_c = _acc_hist[-1].size == 2 * _acc_idx.size
                                _smm_c = np.concatenate([_acc_last["sm"]]
                                                        * (2 if _two_c else 1))
                                _seq_c = [_h[_smm_c] for _h in _acc_hist[-3:]]
                                _q_c, _s_c, _r_c = _acc_cycles(_acc_last["perm"],
                                                               _acc_last["sign"])
                                _m0_c = [_acc_comp(_x, _acc_last["perm"],
                                                   _acc_last["sign"], _q_c, _s_c,
                                                   _r_c)["m0"] for _x in _seq_c]
                                _inf_c = _acc_single(_m0_c, _acc_last["w"])[1]
                                _lam_s = (_inf_c.get("lambda")
                                          if not _inf_c.get("refused") else None)
                            except Exception as _e_chk:   # noqa: BLE001
                                log.warning("P2 eddy accelerator: slow-mode check "
                                            "could not read the state (%s)", _e_chk)
                            if _lam_s and 0.0 < float(_lam_s) < 1.0:
                                _acc_lam_state.append(float(_lam_s))
                            _lam_c = max([0.9] + list(_acc_lams)
                                         + ([float(_lam_s)] if _lam_s else []))
                            _tails = _acc_tail({_gk: _warm_grp[_gk]
                                                for _gk in _acc_last["bodies"]
                                                if _gk in _warm_grp},
                                               int(n_steps_per_period), _lam_c)
                            _acc_check = {"lambda": float("%.4g" % _lam_c),
                                          "lambda_state": (None if _lam_s is None
                                                           else float("%.4g" % _lam_s)),
                                          "per_group": {_gk: float("%.3g" % _v)
                                                        for _gk, _v in _tails.items()}}
                            if max(_tails.values(), default=0.0) > _EDDY_SETTLE_TOL:
                                _warm_quiet = False
                                _warm_resid = max(_warm_resid, max(_tails.values()))
                                log.info("P2 eddy accelerator: the gauge passes but the "
                                         "slow-mode tail at the measured lambda %.3f is "
                                         "%s -- not settled", _lam_c,
                                         {_gk: "%.3g %%" % (100.0 * _v)
                                          for _gk, _v in _tails.items()})
                        elif _warm_quiet and _eec_lam and _eec_last:
                            # after a DC correction (an ACCELERATOR, verified
                            # on the original march):
                            # (1) the loss tail of EVERY corrected body at the
                            #     slowest λ the correction measured;
                            # (2) the period-map residual in the STATE: the
                            #     pole-pair-periodic change of each corrected
                            #     body over the period just marched, as a
                            #     geometric bound on the DC error left
                            #     (periodic_accel.period_map_residual).
                            _lam_c = max([0.9] + list(_eec_lam))
                            _tails = _acc_tail({_gk: _warm_grp[_gk]
                                                for _gk in _eec_last["bodies"]
                                                if _gk in _warm_grp},
                                               int(n_steps_per_period), _lam_c)
                            _pmr = {}
                            if _eec_x0 is not None:
                                for _gk, (_bi_r, _pm_r, _sg_r, _q_r, _s_r,
                                          _M_r) in _eec_last["maps"].items():
                                    _pmr[_gk] = _acc_pm_resid(
                                        _acc_image_mean((A2 - _eec_x0)[_bi_r],
                                                        _pm_r, _sg_r, _q_r, _s_r),
                                        _acc_image_mean(A2[_bi_r], _pm_r, _sg_r,
                                                        _q_r, _s_r),
                                        _M_r, _lam_c)
                            _acc_check = {"lambda": float("%.4g" % _lam_c),
                                          "per_group": {_gk: float("%.3g" % _v)
                                                        for _gk, _v in _tails.items()},
                                          "period_map_residual": {
                                              _gk: float("%.3g" % _v)
                                              for _gk, _v in _pmr.items()}}
                            _worst_c = max(list(_tails.values()) + list(_pmr.values()),
                                           default=float("inf"))
                            if not _pmr or _worst_c > _EDDY_SETTLE_TOL:
                                _warm_quiet = False
                                _warm_resid = max(_warm_resid, _worst_c)
                                log.info("P2 eddy DC correction: the gauge passes but "
                                         "the slow-mode tail at lambda %.3f is %s and "
                                         "the period-map residual %s -- not settled",
                                         _lam_c,
                                         {_gk: "%.3g %%" % (100.0 * _v)
                                          for _gk, _v in _tails.items()},
                                         {_gk: "%.3g %%" % (100.0 * _v)
                                          for _gk, _v in _pmr.items()})
                    _quiet = (_warm_quiet
                              and not (demag and _dm_moved_in_warm))
                    if _ref_ok and not (_warm_resid <= _EDDY_SETTLE_TOL):
                        # the verdict came from the seed's same-angle reference
                        _warm_resid = float(_ref_dev)
                    _warm_gauge = {
                        "method": ("same_angle_reference"
                                   if (_ref_ok and _warm_nper < 2)
                                   else "whole_period_means_per_group"),
                        "whole_periods": int(_warm_nper),
                        "per_group": {_gk: (None if _gr is None
                                            else float("%.3g" % _gr))
                                      for _gk, _gr in _warm_gres.items()},
                        "extension_periods": int(_warm_ext_periods),
                        "max_extension_periods": int(_eddy_cap_periods),
                        "accelerator": ({"method": ("dc_error_correction"
                                                    if _eec_on else "rre_period_map"),
                                         "jumps": list(_acc_jumps),
                                         "slow_mode_check": _acc_check,
                                         "slow_body_cap": (dict(_eddy_slow_cap)
                                                           if _eddy_slow_cap
                                                           else None),
                                         "periods_since_last_jump": (
                                             int(_warm_nper) if any(
                                                 _j.get("applied")
                                                 for _j in _acc_jumps)
                                             else None)}
                                        if _acc_jumps else None),
                        "period_remap": (None if "map" not in _shift_cache
                                         else bool(_shift_cache["map"]
                                                   is not None)),
                    }
                    _Npm = int(n_steps_per_period)
                    log.info("P2 eddy warm-up: %d frame(s) solved (%d whole "
                             "period(s) continuous), remaining start-up "
                             "transient %s (tol %.1f %%) per group %s; last "
                             "period means [W] %s",
                             _n_warm, _warm_nper,
                             ("unmeasured" if not math.isfinite(_warm_resid)
                              else "%.3g %%" % (100.0 * _warm_resid)),
                             100.0 * _EDDY_SETTLE_TOL,
                             {_gk: (None if _gr is None else "%.3g %%"
                                    % (100.0 * _gr))
                              for _gk, _gr in _warm_gres.items()},
                             {_gk: ["%.6g" % float(np.mean(
                                 _gv[len(_gv) - (_j + 1) * _Npm:
                                     len(_gv) - _j * _Npm]))
                                 for _j in range(min(3, len(_gv) // _Npm) - 1,
                                                 -1, -1)]
                              for _gk, _gv in _warm_grp.items()})
                    # raise the cap for a SLOW body (see its init): the
                    # slowest λ the accelerator measured on this state
                    if (not _quiet and not _eddy_cap_pinned and not _eddy_slow_cap
                            and _EDDY_MAX_WARM_PERIODS_SLOW > _eddy_cap_periods):
                        _lam_m = [float(_l) for _l in (list(_acc_lams)
                                                      + list(_acc_lam_state))
                                  if 0.0 < float(_l) < 1.0]
                        if _lam_m:
                            _lam_x = max(_lam_m)
                            _tau_x = -1.0 / math.log(_lam_x)
                            if _tau_x > _EDDY_MAX_WARM_PERIODS:
                                _eddy_slow_cap = {
                                    "normal_cap": int(_EDDY_MAX_WARM_PERIODS),
                                    "cap": int(_EDDY_MAX_WARM_PERIODS_SLOW),
                                    "lambda": float("%.4g" % _lam_x),
                                    "tau_periods": float("%.3g" % _tau_x),
                                    "after_period": int(_warm_ext_periods)}
                                _eddy_cap_periods = _EDDY_MAX_WARM_PERIODS_SLOW
                                log.info("P2 eddy warm-up: SLOW body (accelerator "
                                         "lambda %.4f, tau %.1f periods > cap %d) "
                                         "-- cap raised to %d periods",
                                         _lam_x, _tau_x, _EDDY_MAX_WARM_PERIODS,
                                         _eddy_cap_periods)
                    if ((not _quiet) and _eddy_cap >= 3
                            and _warm_ext_periods < _eddy_cap_periods):
                        # Not settled → march ANOTHER whole electrical period
                        # in front of the window.  The rotor goes back one
                        # period, and its eddy state goes with it through the
                        # exact pole-pair map (`_period_shift`), so the march
                        # stays continuous in time: no kick, and the gauge's
                        # period means stay comparable.  Decided on frame 0
                        # (the probe), frame 0 itself is the step before the
                        # spliced one; decided on θ = −dθ, the period ends
                        # there.
                        if (_eec_on and k < 0 and _eec_J is not None
                                and _eec_x0 is not None
                                and _warm_ext_periods in _eec_at
                                and _eec_nJ == _eddy_cap
                                and _shift_cache.get("map") is not None):
                            # the bodies that are significant (decided when the
                            # average started) AND still unsettled by the gauge
                            _why_b = {}
                            for _gk in _eec_groups:
                                if not _eec_sig.get(_gk, "").startswith("significant"):
                                    _why_b[_gk] = _eec_sig.get(_gk, "not measured")
                                elif not (_warm_gres.get(_gk) is None
                                          or _warm_gres[_gk] > _EDDY_SETTLE_TOL):
                                    _why_b[_gk] = "settled by the gauge"
                                else:
                                    _why_b[_gk] = "corrected"
                            _eg = [_gk for _gk, _w in _why_b.items() if _w == "corrected"]
                            if _eg and not (_eec_dhdb is not None and _eec_dhdb > 0.0):
                                # K + T is not positive definite somewhere
                                # (non-monotone B-H): no correction
                                _acc_jumps.append({
                                    "method": "dc_error_correction",
                                    "after_period": int(_warm_ext_periods),
                                    "applied": False,
                                    "refused": "averaged operator not positive "
                                               "(min dH/dB/nu = %s)" % _eec_dhdb,
                                    "bodies_why": dict(_why_b)})
                                _eg = []
                            if _eg:
                                _Mb_e = None
                                for _gk in _eg:
                                    _Mb_e = (_Msig_grp[_gk] if _Mb_e is None
                                             else _Mb_e + _Msig_grp[_gk])
                                _Mb_e = _Mb_e.tocsr()
                                _bi = np.flatnonzero(np.asarray(
                                    _Mb_e.diagonal()).ravel() > 0.0)
                                _mp_e = _shift_cache["map"]
                                _pm_e, _sg_e = _acc_restrict(_mp_e[0], _mp_e[1],
                                                             _mp_e[2], _bi)
                                _q_e, _s_e, _ = _acc_cycles(_pm_e, _sg_e)
                                _Te = float(_eddy_cap) * float(dt)
                                _Jb = (_eec_J * (1.0 / float(_eec_nJ))).tocsr()
                                _Jbf = (Pro.T @ _Jb @ Pro).tocsr()[_free2][:, _free2].tocsc()
                                _Mbb = _Mb_e[_bi][:, _bi].tocsr()
                                _PtM = (Pro.T @ _Mb_e[:, _bi]).tocsr()[_free2]

                                # UNSYMMETRIC LU on purpose (spd=False): the
                                # unclamped tangent is positive definite only
                                # where dH/dB > 0, which is checked above over
                                # the averaged frames, not guaranteed by
                                # construction like the Newton operators that
                                # take the Cholesky path.  Two corrections of
                                # <= 61 solves on one pattern: nothing to gain.
                                def _solve_JM(v):
                                    return _p2.pad2(Pro, _free2, _p2.solve_ff(
                                        _Jbf, np.asarray(_PtM @ v).ravel(),
                                        spd=False))[_bi]
                                _u_b = (A2 - _eec_x0)[_bi]
                                _corr_b, _ie = _acc_dc_eec(_u_b, _solve_JM, _Mbb,
                                                           _pm_e, _sg_e, _q_e, _s_e,
                                                           _Te)
                                _lam_e = _ie.get("lambda")
                                _ok_e = bool(_lam_e is not None and 0.0 < _lam_e < 0.999
                                             and not _ie.get("refused"))
                                if _ok_e:
                                    # a time-constant (rotor-frame DC) correction:
                                    # added to BOTH BDF2 history levels (A2 =
                                    # A_k, _Aed_prev = A_{k-1}; the splice
                                    # below carries both across the period), so
                                    # the discrete dA/dt of the march is not
                                    # kicked -- only the DC level moves
                                    _corr = np.zeros(N2); _corr[_bi] = _corr_b
                                    A2 = A2 + _corr
                                    _Aed_prev = _Aed_prev + _corr
                                    _eec_last = {"bodies": list(_eg), "maps": {}}
                                    for _gk in _eg:
                                        _Mg_r = _Msig_grp[_gk].tocsr()
                                        _bg = np.flatnonzero(np.asarray(
                                            _Mg_r.diagonal()).ravel() > 0.0)
                                        _pg_r, _sgn_r = _acc_restrict(
                                            _mp_e[0], _mp_e[1], _mp_e[2], _bg)
                                        _qg_r, _sg2_r, _ = _acc_cycles(_pg_r, _sgn_r)
                                        _eec_last["maps"][_gk] = (
                                            _bg, _pg_r, _sgn_r, _qg_r, _sg2_r,
                                            _Mg_r[_bg][:, _bg].tocsr())
                                    _warm_grp = {}          # the gauge restarts
                                    _eec_lam.append(float(_lam_e))
                                    _acc_lams.append(float(_lam_e))
                                _ent = {_kk: (float("%.4g" % _vv) if isinstance(_vv, float)
                                              else _vv) for _kk, _vv in _ie.items()}
                                _ent.update({"after_period": int(_warm_ext_periods),
                                             "bodies": list(_eg), "dofs": int(_bi.size),
                                             "bodies_why": dict(_why_b),
                                             "dhdb_min_rel": (None if _eec_dhdb is None
                                                              else float("%.3g" % _eec_dhdb)),
                                             "frames_averaged": int(_eec_nJ),
                                             "applied": _ok_e})
                                _acc_jumps.append(_ent)
                                log.info("P2 eddy DC error correction after "
                                         "extension period %d: %s", _warm_ext_periods,
                                         _ent)
                        _eec_J = None; _eec_nJ = 0; _eec_dhdb = None
                        _warm_extended = True
                        _warm_ext_periods += 1
                        # the DC correction's Jacobian average runs only in a
                        # period that ends in a correction AND only for a body
                        # the gauge judges on its own level (≥ 1 W or ≥ 2 % of
                        # the machine loss): a milliwatt shaft is left alone
                        _eec_accum = False
                        if _eec_on and _warm_ext_periods in _eec_at:
                            _lm = {_gk: float(np.mean(_gv[-_eddy_cap:]))
                                   for _gk, _gv in _warm_grp.items()
                                   if len(_gv) >= _eddy_cap}
                            _cu_m = abs(_lm.get("cu", 0.0))
                            _mach = (sum(abs(_v) for _gk, _v in _lm.items() if _gk != "cu")
                                     + max(_cu_m, abs(float(_warm_cu_W))))
                            _eec_sig = {}
                            for _gk in _eec_groups:
                                if _gk not in _lm:
                                    _eec_sig[_gk] = "not measured"
                                elif (abs(_lm[_gk]) >= _EDDY_SIG_W
                                      or abs(_lm[_gk]) >= 0.02 * _mach):
                                    _eec_sig[_gk] = "significant (%.4g W)" % _lm[_gk]
                                else:
                                    _eec_sig[_gk] = ("minor body (%.3g W < %g W and "
                                                     "< 2 %% of %.4g W)"
                                                     % (_lm[_gk], _EDDY_SIG_W, _mach))
                            _eec_accum = any(_v.startswith("significant")
                                             for _v in _eec_sig.values())
                        if k >= 0:
                            _n_warm += 1           # frame 0 was warm-up too
                            _fseq[_fi:_fi] = (list(range(-_eddy_cap + 1, 0))
                                              + [0])
                        else:
                            _fseq[_fi:_fi] = list(range(-_eddy_cap, 0))
                        _dm_moved_in_warm = False   # a fresh window judges fresh
                        # BOTH history levels cross the period (BDF2 needs
                        # A_{k−2} too): the frame before this one, shifted,
                        # is the level before the spliced march's first step.
                        if _eddy_bdf2:
                            _Aed_prev2 = _period_shift(_Aed_prev)
                            _hed_prev = _h_ed
                        _Ib_prev2 = _Ib_prev; _Ib_prev = _Ib_k        # stator currents; rotor ∫J=0
                        _Aed_prev = _period_shift(A2)
                        _A2_prev = _period_shift(A2)
                        _eec_x0 = (_Aed_prev.copy() if _eec_on else None)
                        # ── periodic-state accelerator (see its init) ──────
                        # Only on the θ = −dθ decisions (one phase of the
                        # period map), never on the frame-0 probe.
                        if _acc_on and k < 0:
                            _acc_seen += 1
                            if _acc_idx is None:
                                _rot_M = None
                                for _gk in ("shaft", "sleeve", "mag"):
                                    if _gk in _Msig_grp:
                                        _rot_M = (_Msig_grp[_gk] if _rot_M is None
                                                  else _rot_M + _Msig_grp[_gk])
                                if _rot_M is None or _shift_cache.get("map") is None:
                                    _acc_on = False     # nothing slow / no exact map
                                    log.info("P2 eddy accelerator: off (%s)",
                                             "no rotor conductor" if _rot_M is None
                                             else "no exact period map")
                                else:
                                    _dg = np.asarray(_rot_M.diagonal()).ravel()
                                    _acc_idx = np.flatnonzero(_dg > 0.0)
                                    _acc_w = _dg[_acc_idx]
                                    _acc_gmask = {
                                        _gk: (np.asarray(_Msig_grp[_gk].diagonal()
                                                         ).ravel()[_acc_idx] > 0.0)
                                        for _gk in ("shaft", "sleeve", "mag")
                                        if _gk in _Msig_grp}
                            if _acc_on:
                                # this period's iterate of the map (both levels)
                                _two = bool(_eddy_bdf2 and _Aed_prev2 is not None)
                                _x_now = np.concatenate(
                                    [_Aed_prev[_acc_idx]]
                                    + ([_Aed_prev2[_acc_idx]] if _two else []))
                                _acc_hist = (_acc_hist + [_x_now])[-4:]
                            if _acc_on and _acc_skip_left > 0:
                                _acc_skip_left -= 1
                            elif _acc_on:
                                _acc_states.append(_x_now)
                                _acc_dec.append(int(_acc_seen))
                                _room = (_eddy_cap_periods - _warm_ext_periods
                                         + 1 >= _acc_min_verify)
                                # the SLOW bodies: the rotor conductor groups
                                # the gauge has just called unsettled.  A fast
                                # body (magnets, sleeve) is left to the march:
                                # its period changes are Newton-level noise
                                # that would swamp the slow mode's least squares.
                                _slow = [_gk for _gk in _acc_gmask
                                         if (_warm_gres.get(_gk) is None
                                             or _warm_gres[_gk] > _EDDY_SETTLE_TOL)]
                                if (len(_acc_states) >= _acc_k_now + 2 and _room
                                        and _slow):
                                    _nI = _acc_idx.size
                                    _sm = np.zeros(_nI, bool)
                                    for _gk in _slow:
                                        _sm |= _acc_gmask[_gk]
                                    _sI = _acc_idx[_sm]
                                    _smm = np.concatenate([_sm] * (2 if _two else 1))
                                    # RRE per symmetry sector of the exact
                                    # pole-pair image map (periodic_accel)
                                    _seq = [_st[_smm] for _st in _acc_states]
                                    _wS = np.concatenate([_acc_w[_sm]]
                                                         * (2 if _two else 1))
                                    try:
                                        _rdf_m, _jj_m, _ss_m = _shift_cache["map"]
                                        _pm, _pg_s = _acc_restrict(_rdf_m, _jj_m,
                                                                   _ss_m, _sI)
                                        if _two:
                                            _pm = np.concatenate([_pm, _pm + _sI.size])
                                            _pg_s = np.concatenate([_pg_s, _pg_s])
                                        _msec = None
                                        if (_acc_sec and _acc_sec.get("bodies") == list(_slow)
                                                and len(_seq) >= 2):
                                            _msec = {"u_pre": _acc_sec["u_pre"],
                                                     "S_p": _acc_sec["S_p"],
                                                     "n_after": int(_acc_dec[0])
                                                     - int(_acc_sec["dec"])}
                                        _acc_sec = {}
                                        _xs, _ai = _acc_rre_sec(_seq, _wS, _pm, _pg_s,
                                                                m0_secant=_msec)
                                        _ai["method"] = "rre_per_symmetry_sector"
                                    except ValueError as _e_sec:
                                        log.warning("P2 eddy accelerator: image map "
                                                    "not closed on the slow bodies "
                                                    "(%s) — one RRE over the whole "
                                                    "state", _e_sec)
                                        _xs, _ai = _acc_rre(_seq, _wS)
                                        _ai["method"] = "rre_whole_state"
                                        _ai.pop("gamma", None)
                                    _nS = _sI.size
                                    _ai.update({"after_period": int(_warm_ext_periods - 1),
                                                "states": len(_acc_states),
                                                "bodies": list(_slow),
                                                "dofs": int(_nS)})
                                    if _xs is not None:
                                        _acc_last = {"sm": _sm, "perm": _pm,
                                                     "sign": _pg_s, "bodies": list(_slow),
                                                     "w": _wS}
                                        for _sk, _se in (_ai.get("sectors") or {}).items():
                                            if (_se.get("applied") and _se.get("lambda")
                                                    and str(_se.get("method", "")
                                                            ).startswith("single_mode")):
                                                _acc_lams.append(float(_se["lambda"]))
                                            if "_u_pre" in _se:
                                                _acc_sec = {"u_pre": _se["_u_pre"],
                                                            "S_p": _se["S_step"],
                                                            "dec": int(_acc_dec[-1]),
                                                            "bodies": list(_slow)}
                                        _Aed_prev = _Aed_prev.copy()
                                        _Aed_prev[_sI] = _xs[:_nS]
                                        _A2_prev = _A2_prev.copy()
                                        _A2_prev[_sI] = _xs[:_nS]
                                        if _two:
                                            _Aed_prev2 = _Aed_prev2.copy()
                                            _Aed_prev2[_sI] = _xs[_nS:]
                                        # the gauge judges ONLY periods solved
                                        # after the jump (≥ MIN_VERIFY_PERIODS)
                                        _warm_grp = {}
                                        _ai["applied"] = True
                                    else:
                                        _ai["applied"] = False
                                    for _se in (_ai.get("sectors") or {}).values():
                                        _se.pop("_u_pre", None)
                                    _acc_states = []
                                    _acc_dec = []
                                    if _ai["applied"]:
                                        # a jump of a multi-mode (RRE) sector
                                        # re-excites fast modes: skip; a
                                        # single-mode step moves along the
                                        # slow mode only: collect at once
                                        _acc_skip_left = (_ACC_POST_SKIP if any(
                                            _se.get("applied") and _se.get("method") == "rre"
                                            for _se in (_ai.get("sectors") or {}).values())
                                            else 0)
                                        _acc_k_now = _ACC_K2
                                        if _acc_sec:
                                            # after a single-mode jump: one
                                            # period, then ONE change calibrates
                                            # it (periodic_accel.secant_step_scale)
                                            _acc_skip_left = max(_acc_skip_left, 1)
                                            _acc_k_now = 0
                                    _acc_jumps.append(
                                        {_kk: (_vv if not isinstance(_vv, float)
                                               else float("%.4g" % _vv))
                                         for _kk, _vv in _ai.items()
                                         if _kk not in ("u_norms",)})
                                    log.info("P2 eddy accelerator: RRE over %d "
                                             "period states after extension "
                                             "period %d — %s", _ai["states"],
                                             _ai["after_period"],
                                             ("jump applied (%s), step %.3g x the "
                                              "last change; sectors %s; the gauge "
                                              "restarts"
                                              % (_ai.get("method"),
                                                 _ai["step_over_last_change"],
                                                 _ai.get("sectors")))
                                             if _ai["applied"] else
                                             "REFUSED (%s), marching on"
                                             % _ai.get("refused"))
                                    log.info("P2 eddy accelerator: bodies %s, %d dofs",
                                             _slow, _nS)
                        log.info("P2 eddy warm-up: not settled — extending by "
                                 "one electrical period (%d frames, extension "
                                 "%d of at most %d)", _eddy_cap,
                                 _warm_ext_periods, _eddy_cap_periods)
                        continue
                    _warm_done = True
                    if not _quiet:
                        # NAME THE UNSETTLED PARTY.  The quiet test now watches
                        # two settlings, and blaming the eddy transient for a
                        # Br ratchet that simply finished its first pass over
                        # the rotor positions (expected: the ratchet MUST visit
                        # every position once) produced a warning that read
                        # "0.8 % over a 2 % tolerance".
                        if _warm_resid > _EDDY_SETTLE_TOL:
                            log.warning(
                                "P2 eddy warm-up: STILL NOT SETTLED after %d "
                                "frame(s) — %.3g %% of the solid loss at the "
                                "handoff is decaying start-up transient (tol "
                                "%.1f %%).  P_mag / P_shaft and the efficiency "
                                "are over-read by roughly that much.", _n_warm,
                                100.0 * _warm_resid, 100.0 * _EDDY_SETTLE_TOL)
                        else:
                            # The eddy side IS quiet; only the demag ratchet
                            # moved inside the warm-up window.  Correctness does
                            # not depend on it — a residual trip inside the
                            # reported window re-solves that frame — so this is
                            # an economy note, not an alarm.
                            log.info(
                                "P2 warm-up handoff: eddy settled (%.3g %% <= "
                                "%.1f %%); the demag ratchet completed during "
                                "the warm-up march, reported window starts on "
                                "the settled magnet.", 100.0 * _warm_resid,
                                100.0 * _EDDY_SETTLE_TOL)
                    # ── DEMAG PRE-PASS (user's bug, 2026-09-05) ───────────
                    # The eddy handoff has happened, quiet or not: the field
                    # this frame carries is the best settled state this run
                    # will ever have.  Only NOW may the irreversible ratchet
                    # look.  One whole electrical period at θ<0, so every
                    # magnet passes every stator position once, with the eddy
                    # history CONTINUED (nothing is reset — the magnet
                    # weakening is itself a slow excitation the σ·∂A/∂t field
                    # follows through these frames) and the frames discarded.
                    # Spliced exactly like the warm-up extension above: the
                    # march ends at k = −1 so frame 0 is handed a field one dt
                    # old, and frame 0 is re-queued when the handoff decision
                    # was taken ON it (the non-extended case), because its
                    # first solve saw the pristine magnet.
                    if _dm_freeze_warm and not _dm_prepass:
                        _dm_prepass = True
                        _dm_ratchet = True          # unfrozen from here on
                        _n_dmpre += _dm_pre_len
                        _dm_pass_br0 = _br_glob.copy()   # the fixed-point measure
                        if k >= 0:
                            _n_warm += 1            # this frame is re-solved
                        # Continuous in time, like the extension: the eddy
                        # state crosses the period through the pole-pair map
                        # (the Br map does NOT move — it is the material's).
                        if _dm_pre_len > 0:
                            if k >= 0:
                                _fseq[_fi:_fi] = (
                                    list(range(-_dm_pre_len + 1, 0)) + [0])
                            else:
                                _fseq[_fi:_fi] = list(range(-_dm_pre_len, 0))
                            if _eddy_bdf2:           # both levels, as above
                                _Aed_prev2 = _period_shift(_Aed_prev)
                                _hed_prev = _h_ed
                            _Ib_prev2 = _Ib_prev; _Ib_prev = _Ib_k
                            _Aed_prev = _period_shift(A2)
                            _A2_prev = _period_shift(A2)
                        elif k >= 0:
                            # Seeded magnet, no pre-pass: re-solve THIS frame
                            # with the ratchet on, from the SAME history it was
                            # just solved from (`_Aed_prev`, `_Aed_prev2` are
                            # untouched).
                            _fseq[_fi:_fi] = [0]
                        else:
                            if _eddy_bdf2:
                                _Aed_prev2 = _Aed_prev; _hed_prev = _h_ed
                            _Ib_prev2 = _Ib_prev; _Ib_prev = _Ib_k
                            _Aed_prev = A2.copy()   # plain continuation
                        log.info(
                            "P2 demag %s: %d frame(s) at θ<0 with the Br "
                            "ratchet ACTIVE, on the settled eddy field (the "
                            "%d warm-up frame(s) ran it FROZEN).",
                            ("ratchet unfrozen at the handoff (pre-pass "
                             "SKIPPED — seeded magnet)" if _dm_seeded
                             else "pre-pass"), _dm_pre_len, _n_warm)
                        continue
            # ── DEMAG FIXED POINT (march side; see `_dm_settle_tol`) ──────
            # The end of a pre-pass period (k = −1, the handoff frame itself
            # never reaches here): if that period moved Br by more than the
            # tolerance, splice ANOTHER period in, continuous in time — the eddy
            # state one period back (`_period_shift`) and the Br map relabelled
            # by the same pole-pair map, so every magnet goes on to the rotor
            # positions it meets next.
            if (_dm_prepass and _dm_pre_len > 0 and k < 0 and not _dm_pass_done
                    and _dm_pass_br0 is not None and _dmst is not None):
                # this pre-pass frame's torque, for the drift check
                _dm_tq_cur[int(k)] = _dm_frame_torque(
                    A2, Ist, math.radians(float(theta_eff)))
            if (_dm_prepass and _dm_pre_len > 0 and k == -1 and not _dm_pass_done
                    and _dm_pass_br0 is not None and _dmst is not None):
                _dm_c = _dm_change(_dm_pass_br0, _br_glob)
                _dm_c["frames"] = int(_dm_pre_len)
                _dm_c["torque"] = _dm_take_torque()
                _dm_passes.append(_dm_c)
                if (not _dm_pass_ok(_dm_c, _dm_passes)
                        and len(_dm_passes) < _dm_cap()):
                    log.info("P2 demag pre-pass period %d moved Br by %.3g (worst "
                             "magnet, area mean; tol %.1g), %.3g at one element "
                             "(tol %.1g); image history %s; drift %s — one more "
                             "period",
                             len(_dm_passes), _dm_c["per_magnet_mean_max"],
                             _dm_settle_tol, _dm_c["element_max"], _dm_settle_etol,
                             _dm_c.get("image_history"), _dm_c.get("drift"))
                    _br_glob[:] = _dm_relabel(_br_glob)
                    _mx_all[nst:] = _Mx_glob * _br_glob
                    _my_all[nst:] = _My_glob * _br_glob
                    f_mag2 = asm(_msrc, b2, mx=b2_0.interpolate(_mx_all),
                                 my=b2_0.interpolate(_my_all))
                    _drv.f_mag = f_mag2
                    _fseq[_fi:_fi] = list(range(-_dm_pre_len, 0))
                    if _eddy_bdf2:
                        _Aed_prev2 = _period_shift(_Aed_prev)
                        _hed_prev = _h_ed
                    _Ib_prev2 = _Ib_prev; _Ib_prev = _Ib_k
                    _Aed_prev = _period_shift(A2)
                    _A2_prev = _period_shift(A2)
                    _n_dmpre += _dm_pre_len
                    _dm_pass_br0 = _br_glob.copy()
                    continue
                _dm_pass_done = True
            # ── the SAME integrand, kept per element (Loss map) ───────────
            # E = −∂A/∂t + U_b at this element's quadrature points; σE²
            # integrated over the element and divided by its area is the
            # element's loss DENSITY [W/m³] — intensive, so no sector or
            # stack-length factor belongs here (the volume integral below
            # puts them back).
            # Only the field snapshot consumes this history; animation frames
            # carry A/B without a loss map. Keep all frames when one is requested.
            if return_field and _ed_elems.size and k >= 0:
                _Uel = np.zeros(_ed_elems.size)
                for _ci, _c in enumerate(_ed_con):
                    _Uel[_ed_uloc[_ci]] = float(_Uev[_ci]) * _ed_usgn[_ci]
                _Eq = -np.asarray(_ed_basis.interpolate(_dAe)) + _Uel[:, None]
                _ed_dens_hist.append(
                    _ed_sig_e * np.sum(_Eq ** 2 * _ed_dx, axis=1)
                    / np.maximum(_ed_area, 1e-30))
            if _eddy_bdf2:
                _Aed_prev2 = _Aed_prev; _hed_prev = _h_ed
            _Ib_prev2 = _Ib_prev; _Ib_prev = _Ib_k
            _Aed_prev = A2.copy()
            if _tdm_rep is not None and k >= 0:
                # the reported frame, for the TDM acceptance gate after the loop
                _tdm_rep[int(k)] = (A2.copy(), np.asarray(_Ued, float).copy())
            if (_eddy_bdf2 and not _vdrive and k == n_total - 4
                    and n_total >= 8 and not _warm_cache_disabled()):
                _wc_A2 = A2.astype(float, copy=True)   # the seed's A_{k−2}
            if (eddy and not _vdrive and k == n_total - 3 and n_total >= 8
                    and not _warm_cache_disabled()):
                # Stash this frame (angle ≡ −3 steps) for the NEXT run's seed;
                # published after the march, when the per-frame solid losses of
                # the whole measured window exist (the same-angle reference the
                # next run's probe is judged against).
                _wc_A = A2.astype(float, copy=True)
                _wc_Ued = np.asarray(_Ued, float).copy()
            if k >= 0:
                _ed_cu.append(_pg.get("cu", 0.0) * _wsc)
                _ed_mag.append(_pg.get("mag", 0.0) * _wsc)
                _ed_sh.append(_pg.get("shaft", 0.0) * _wsc)
                _ed_sl.append(_pg.get("sleeve", 0.0) * _wsc)
                # DC reference of the SAME bars: with ∂A/∂t = 0 the constraint
                # gives U_b = I_b/S_b and P = ΣI_b²/S_b — the 2-D (active
                # length only) I²R, so total − this is the honest AC increment
                # to add to the end-winding-corrected DC below.
                # (at the instant the loss was sampled — the step midpoint
                # under BDF2, so total − DC stays the AC increment)
                _ed_dc2d.append(float(np.sum(
                    [_Idc_e[_ci] ** 2 / max(c["S"], 1e-30)
                     for _ci, c in enumerate(_ed_con)
                     if c["key"] == "cu"])) * _wsc)
        if k < 0:
            if k == -1:
                # The frame solved immediately before frame 0 (eddy warm-up /
                # demag pre-pass): the step voltage of frame 0 is measured
                # against it, as the time march itself did.
                _pa_m, _pb_m, _pc_m = _psi2(A2)
                _pre_frame = {"psi": (_pa_m, _pb_m, _pc_m),
                              "I": (Ist['A'], Ist['B'], Ist['C'])}
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
        _frame_converged.append(bool(_newton_ok or _res < _PIC_TOL2))
        # Per-frame convergence trace.  picard_resid_max alone says only THAT
        # some frame was the worst; when it lands near the tol you need to know
        # WHICH frame and by WHICH path, so log it (DEBUG — one line per frame).
        log.debug("P2 frame %d: %s, %d its, res=%.3e",
                  k, "newton" if _newton_ok else "picard", _nit, _res)
        _vw_reason = ("disabled_set_SB_P2_VIRTUAL_WORK=1" if not _vw_enabled
                      else _p2_virtual_work_ineligible_reason(
                          eddy=bool(eddy), voltage_drive=bool(_vdrive),
                          demag=bool(demag), frozen_nu=bool(frozen_nu),
                          saturable=bool(_sat2), newton_ok=bool(_newton_ok)))
        _vw_torque = None
        if _vw_reason is None:
            if _vw_action is None:
                _vw_reason = "derivative_initialisation_failed:" + str(_vw_init_error)
            else:
                try:
                    _vw_torque = _p2_virtual_work_torque(
                        _p2, A2, f, _vw_action, m_shift, NS, p.stack_length)
                    if not math.isfinite(_vw_torque):
                        _vw_torque = None
                        _vw_reason = "nonfinite_virtual_work"
                except Exception as _vw_exc:  # noqa: BLE001 — diagnostic only
                    _vw_reason = "virtual_work_failed:" + type(_vw_exc).__name__
                    log.warning("P2 virtual-work diagnostic frame %d failed "
                                "(%s: %s)", k, type(_vw_exc).__name__, _vw_exc)
        _T_vw.append(_vw_torque)
        _T_vw_reason.append(_vw_reason)
        if k >= int(_vskip) + int(_dmskip):
            _nu_r_k = np.asarray(nu_all2, float)[nst:]
            _nu_rot_sum = (_nu_r_k.copy() if _nu_rot_sum is None
                           else _nu_rot_sum + _nu_r_k)
            _nu_rot_n += 1
        _ftk = _frame_torques(_ftq2, A2)
        Tq = _ftk["maxwell_Nm"]
        _T2.append(Tq)
        _Tc2.append(_ftk["coulomb_Nm"])
        _Tc2l.append((_ftk["coulomb_rotor_side_Nm"], _ftk["coulomb_stator_side_Nm"]))
        _pa, _pb, _pc = _psi2(A2)
        _psiA.append(_pa); _psiB.append(_pb); _psiC.append(_pc)
        if f_set2 is not None and _six_blk.get("sets_in_model") == [1, 2]:
            # per-set phase-A flux linkage (one branch of that set) and the
            # electrical angle it was taken at — the per-set voltage, measured
            # (not assumed equal) wherever both sets are in the model
            _six_psi.append((math.radians(theta_eff * pole_pairs),
                             2.0 * _sc_psi2 * float(A2 @ f_set2[1]['A']),
                             2.0 * _sc_psi2 * float(A2 @ f_set2[2]['A'])))
        _IA.append(Ist['A']); _IB.append(Ist['B']); _IC.append(Ist['C'])
        # The Δt this frame's step was SOLVED with (rotor time on a voltage
        # run, the nominal step otherwise) — the step voltage divides by it.
        _dt_steps.append(float(_dt_k if _vdrive else dt))
        _p2_capture = _current_p2_state_capture()
        if _p2_capture is not None:
            _emit_selected_p2_state(
                _p2_capture,
                {
                    "frame_index": int(k),
                    "slip_shift": int(m_shift),
                    "mechanical_angle_deg": float(theta_eff),
                    "mechanical_angle_rad": math.radians(float(theta_eff)),
                    "time_s": float(_sched_t[k]),
                    "current_abc_A": {ph: float(Ist[ph]) for ph in "ABC"},
                    "psi_abc_Wb": {"A": float(_pa), "B": float(_pb),
                                   "C": float(_pc)},
                    "newton_converged": bool(_newton_ok),
                    "iterations": int(_nit),
                    "raw_residual": float(_res),
                    "picard_unconverged": bool(k in _pic_unconv),
                    "eddy": bool(eddy), "demag": bool(demag),
                    "voltage_drive": bool(_vdrive),
                    "imposed_current_drive": not bool(_vdrive),
                    "rotor_eddy": bool(rotor_eddy),
                    "frozen_nu": bool(frozen_nu),
                    "n_parallel": int(n_parallel), "n_sectors": int(NS),
                    "pole_pairs": int(pole_pairs),
                    "stack_length_m": float(p.stack_length),
                    "stator_element_count": int(nst),
                },
                lambda: {
                    "A_z": A2,
                    "mesh_p_m": mesh_all.p,
                    "mesh_t": mesh_all.t,
                    "stator_cell_tags": half["s"]["cells"],
                    "rotor_cell_tags": half["r"]["cells"],
                    "doflocs_m": b2.doflocs,
                    "element_dofs": b2.element_dofs,
                    "quadrature_dx_m2": b2.dx,
                    "nu_base2": nu_base2,
                    "saturable_materials": _sat2,
                    "Hc_x_effective_Apm": _mx_all,
                    "Hc_y_effective_Apm": _my_all,
                    "magnet_source_vector": f_mag2,
                    "coil_source_vectors_per_A": f_coil2,
                    "magnet_br_state": _br_glob,
                    "rotor_vertex_dofs": _rot_vdof,
                    "rotor_node_count": _nr2,
                    "Bx_quad_T": (_capture_grad := b2.interpolate(A2).grad)[1],
                    "By_quad_T": -_capture_grad[0],
                },
            )
        # ── INCREMENTAL d-q INDUCTANCES at this rotor position ───────────
        # Frozen permeability on the field THIS frame just converged (see
        # `frozen_permeability_ldq`): three linear back-solves with the
        # frame's own operator.  It reads state and never writes any, so a
        # failure here costs the run nothing but this diagnostic.
        if k in _inc_at:
            try:
                _th_i = math.radians(theta_eff * pole_pairs + daxis_eff - 90.0)
                _row = frozen_permeability_ldq(
                    _p2, Pro, _free2, A2, f_mag2, _Pa2, _Pb2, _psi2,
                    _th_i, n_parallel=n_parallel)
                _npf_i = float(max(1, int(n_parallel)))
                _row["i_d_A"], _row["i_q_A"] = _park_dq(
                    Ist['A'] * _npf_i, Ist['B'] * _npf_i, Ist['C'] * _npf_i,
                    _th_i)
                _row["psi_d_Wb"], _row["psi_q_Wb"] = _park_dq(
                    _pa, _pb, _pc, _th_i)
                _row["frame"] = int(k)
                _inc_rows.append(_row)
            except Exception as _eil:   # noqa: BLE001 — a diagnostic, never fatal
                log.debug("incremental Ldq failed at frame %d: %s", k, _eil)
            if f_set2 is not None and _six_blk.get("xy_probe_valid"):
                try:
                    _nbs = max(1.0, float(n_parallel) / 2.0)
                    _vr = frozen_permeability_vsd(
                        _p2, Pro, _free2, A2, f_set2,
                        p.stack_length * NS / _nbs, _th_i, _nbs)
                    _vr["frame"] = int(k)
                    _six_rows.append(_vr)
                except Exception as _eiv:   # noqa: BLE001 — diagnostic
                    log.warning("six-phase L_xy probe failed at frame %d: %s",
                                k, _eiv)
                    _six_blk["xy_probe_error"] = f"{type(_eiv).__name__}: {_eiv}"
        # The MEASUREMENT the source gets on the next frame — the machine's own
        # converged values, before any settling bookkeeping moves them.
        _fb_i_prev = {'A': Ist['A'], 'B': Ist['B'], 'C': Ist['C']}
        _fb_psi_prev = {'A': _pa, 'B': _pb, 'C': _pc}
        # Nominal elapsed time of this frame.  A LIST, not k*dt: the settle
        # frames can be coarser than the reported ones, so elapsed time is a
        # cumulative sum of the schedule (identical to k*dt when uniform).
        _tt.append(_sched_t[k] if k >= 0 else k * dt)
        _theta_samples.append(math.radians(float(theta_eff)))
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
            if k in _settle_bounds:
                _v_bpsi.append((_pa, _pb))
                if len(_v_bpsi) >= 3:
                    # Δ²-extrapolation + its "already converged / unstable"
                    # guard: simulation/drive.py, shared with the P1 path.
                    _new, _corr, _drift = _aitken_flux_anchor(_v_bpsi)
                    _v_anchor_tries += 1
                    if _new is None:
                        log.info("P2 vdrive Aitken anchor SKIPPED at period %d "
                                 "(drift %.3g, corr %.3g Wb — converged/unstable)",
                                 len(_v_bpsi), _drift, _corr)
                        _v_bpsi.clear()
                    else:
                        _v_anchor_applied += 1
                        _psi_prev = _new
                        _v_bpsi.clear()   # fresh samples only after re-anchor
                        log.info("P2 vdrive Aitken anchor at period %d: psiA "
                                 "%.4g -> %.4g (|corr| %.3g Wb)",
                                 _v_anchor_applied, _pa, _new['A'], _corr)
            # ── PERIODIC-ORBIT SOLVE OF THE DC MODE (2026-09-26) ─────────
            # The phasor init lands NEAR the orbit, not on it, and the
            # modulator turning on kicks the state off it again by about half
            # the ripple amplitude.  Both leave a stationary-frame DC that
            # decays with τ_e — 27 electrical periods on the L155 — so neither
            # a 12-period settle nor a 2-carrier pre-roll sheds it (B5: the
            # window opened with 43 A of DC on a 433 A fundamental).
            #
            # It is SOLVED for, not waited out and not anchored.  Over a whole
            # period the CN rows telescope: the flux drift y(end) − y(start)
            # is exactly Σv·Δt − R·Σī·Δt, zero on the orbit.  The drift is the
            # measurement, the fixed point of the line-to-line flux's period
            # map is the unknown, and the correction is its Newton step,
            # w = M(I−M)⁻¹·drift, in FLUX.  M is the period map's Jacobian:
            # the product of the linearised CN rows over the period, built
            # from each frame's own INCREMENTAL ∂ψ/∂i (the columns its Newton
            # solved for — no extra solve).  simulation/dc_orbit.py has the
            # derivation.  The same columns move the CN current memory with
            # the flux (it enters the next step through R·Δt/2 only).
            #
            # The period-mean ANCHOR this replaces subtracted the measured DC
            # with a gain learned from its own previous firing, clamped to
            # [0.2, 3].  Its model had no free decay: on a short-τ_e machine
            # the DC it measured over a period was mostly gone by the period's
            # end, it over-corrected, and the reported window opened on its
            # last correction — 1.10 A of DC left on the Ø40 against 0.025 A
            # with it switched off, P_cu −0.9 % (docs/NO_FILTERS_2026-09-24.md
            # item 5).  Here the last settling period is never corrected: it
            # runs free and its drift is the solve's verification.
            #
            # THIS frame's ∂ψ/∂i (the Newton's last columns — incremental, at
            # this frame's saturation, eddy reaction included on the coupled
            # path); the phasor initialiser's only if no Newton has set any.
            _qaL = getattr(_drv, "last_qa", None)
            _qbL = getattr(_drv, "last_qb", None)
            if _qaL is None or _qbL is None:
                _qaL, _qbL = _qa, _qb
            if _dc_orbit is not None and k <= _dc_verify_k:
                # …and the SOURCE's own feedback on the previous step's
                # current (the controller bridge's dead time + device drop —
                # a real DC-mode resistance; missing it made the Newton
                # over-shoot and diverge on the L180 delta, 2026-09-28).
                _dc_orbit.frame(_ll_inductance(_qaL, _qbL), _dt_k,
                                getattr(_src, "ll_feedback_gain", None))
            if _dc_orbit is not None and k in _dc_win:
                _k0 = int(_dc_win[k])                   # first frame of the period
                _off = len(_IA) - 1 - k                 # list index of frame k
                _w = np.asarray(_sched_dt[_k0:k + 1], float)
                _dc_p = [_period_dc(_ser, _off + _k0, _off + k, _w)
                         for _ser in (_IA, _IB, _IC)]
                _corr = (k != _dc_verify_k) and _dc_orbit.correcting
                _wll = _dc_orbit.period_end(
                    _ll_flux((_pa, _pb, _pc)),
                    tag=("fine" if _sched_fine[k] else "coarse"),
                    correct=_corr, frame=int(k), dc_phase_A=_dc_p)
                # THE MODULATOR'S TURN-ON (mixed settle): the next period is
                # the first chopped one.  Its orbit carries the ripple's
                # periodic flux, whose value here is predicted exactly (for a
                # constant inductance) from the modulator's own volt-seconds
                # over that period — dc_orbit.handover_flux_offset — and taken
                # as the Newton predictor; the fine period's drift measures
                # what it missed.  The ideal bridge's volt-seconds: a device's
                # current-dependent dead-time error is left to that drift.
                _mod_h = getattr(_src, "modulator", None)
                if (_corr and _mod_h is not None and not _sched_fine[k]
                        and k + 1 < n_total and _sched_fine[k + 1]):
                    # ONE WHOLE PERIOD on the fine grid from this boundary —
                    # the grid the fine march (its handover step included)
                    # takes: whole slip nodes, so no snapping moves it.
                    _dthf_h = float(period_mech) / float(_v_nspp)
                    _th_h = float(theta_eff) + _dthf_h * np.arange(
                        int(_v_nspp) + 1)

                    def _ripple_h(_a_h, _b_h):
                        # chopped step mean − the fundamental's (Simpson)
                        _vp_h = _mod_h.mean_voltages(_a_h, _b_h)
                        _f_h = [_mod_h.fundamental(_x) for _x in
                                (_a_h, 0.5 * (_a_h + _b_h), _b_h)]
                        return [_vp_h[_ph] - (_f_h[0][_ph] + 4.0 * _f_h[1][_ph]
                                              + _f_h[2][_ph]) / 6.0
                                for _ph in ('A', 'B', 'C')]

                    _wh = _handover_flux_offset(
                        np.asarray([_ripple_h(_th_h[_j], _th_h[_j + 1])
                                    for _j in range(int(_v_nspp))]),
                        np.full(int(_v_nspp), 1.0 / (
                            max(float(f_elec), 1e-9) * float(_v_nspp))))
                    _dc_orbit.note_handover(_wh)
                    _wll = _wh if _wll is None else _wll + _wh
                if _wll is not None:
                    _dpsi, _di = _flux_shift_to_state(_wll, _qaL, _qbL)
                    _psi_prev = {_ph: _psi_prev[_ph] + _dpsi[_ph]
                                 for _ph in ('A', 'B', 'C')}
                    _iv_state = {_ph: _iv_state[_ph] + _di[_ph]
                                 for _ph in ('A', 'B', 'C')}
                _rec = _dc_orbit.records[-1]
                (log.warning if _rec["verdict"].startswith("REFUSED")
                 else log.info)(
                    "P2 vdrive DC-orbit solve at frame %d (%s period, %d "
                    "frames): DC iA %+.4f iB %+.4f iC %+.4f A, drift %.4g Wb, "
                    "period Jacobian eigenvalues %s -> %s%s%s", k,
                    _rec["resolution"], k + 1 - _k0, _dc_p[0], _dc_p[1],
                    _dc_p[2], float(np.max(np.abs(_rec["drift_Wb"]))),
                    ", ".join("%.4f%+.4fj" % tuple(_e)
                              for _e in _rec["decay_per_period"]),
                    _rec["verdict"],
                    ("" if _wll is None else " (%.4g Wb)" % float(
                        np.max(np.abs(_wll)))),
                    ("" if "handover_prediction_Wb" not in _rec else
                     "; incl. the modulator turn-on predictor %.4g Wb"
                     % float(np.max(np.abs(_rec["handover_prediction_Wb"])))))
            # ── CONVERGED SETTLE (plain sinusoidal voltage, 2026-09-26) ──
            # At the end of every settling period, measure the three reported
            # quantities over it and compare with the period before.  A pair
            # counts only when BOTH periods ran after the last Aitken anchor
            # (an anchor moves the state; a pair straddling it measures the
            # anchor, not the orbit), and the settle never stops on a boundary
            # where an anchor just fired (the next period would start on an
            # unmeasured state).  On a stop the schedule is rebuilt for the
            # periods actually marched — a prefix of the capped one, frame for
            # frame — so the run is the fixed-count run with that count.
            if (_conv_settle is not None and _conv_settle["periods_used"] is None
                    and _v_nspp > 0 and 0 <= k < int(_vskip)
                    and (k + 1) % int(_v_nspp) == 0):
                _cs_p = (k + 1) // int(_v_nspp)         # settle periods done
                _cs_o = len(_IA) - 1 - k                # list index of frame k
                _cs_a, _cs_b = _cs_o + k + 1 - int(_v_nspp), _cs_o + k + 1
                _cs_met = _settle_period_metrics(
                    _T2[_cs_a:_cs_b], _IA[_cs_a:_cs_b], _IB[_cs_a:_cs_b],
                    _IC[_cs_a:_cs_b], _dt_steps[_cs_a:_cs_b])
                _cs_anch = int(_v_anchor_applied) != int(
                    _conv_settle.get("_anchors_seen", 0))
                _cs_row = {"period": int(_cs_p),
                           **{_kk: (None if _vv is None else round(float(_vv), 9))
                              for _kk, _vv in _cs_met.items()},
                           "anchor_after": bool(_cs_anch), "residual": None}
                _cs_hist = _conv_settle["history"]
                # A pair counts only on UNPERTURBED periods: the first period
                # after an anchor carries the jump's transient, so the earlier
                # of the pair must start at least one whole period after it.
                _cs_la = int(_conv_settle["last_anchor_period"])
                if _cs_hist and (_cs_la == 0 or _cs_p - 1 > _cs_la + 1):
                    _cs_rs = _settle_period_residual(_cs_hist[-1]["_m"], _cs_met)
                    _cs_row["residual"] = {_kk: float(_vv)
                                           for _kk, _vv in _cs_rs.items()}
                _cs_row["_m"] = _cs_met
                _cs_hist.append(_cs_row)
                if _cs_anch:
                    _conv_settle["last_anchor_period"] = int(_cs_p)
                    _conv_settle["_anchors_seen"] = int(_v_anchor_applied)
                _cs_ok = (_cs_row["residual"] is not None and not _cs_anch
                          and _cs_row["residual"]["max"]
                          <= float(_conv_settle["tol_rel"]))
                if _cs_ok or (k + 1) == int(_vskip):
                    _conv_settle["periods_used"] = int(_cs_p)
                    _conv_settle["converged"] = bool(_cs_ok)
                    _conv_settle["stop_residual"] = _cs_row["residual"]
                    if _cs_ok:
                        log.info("P2 sine settle CONVERGED after %d period(s): "
                                 "period-to-period dI %.2e, dT %.2e, dripple "
                                 "%.2e (tol %.1e)", _cs_p,
                                 _cs_row["residual"]["I_amp"],
                                 _cs_row["residual"]["T_mean"],
                                 _cs_row["residual"]["T_ripple"],
                                 _conv_settle["tol_rel"])
                    else:
                        log.warning(
                            "P2 sine settle hit its CAP of %d periods without "
                            "converging (last period-to-period change %s, tol "
                            "%.1e): the reported window is what the cap "
                            "reached, and the result says so", _cs_p,
                            ("%.2e" % _cs_row["residual"]["max"])
                            if _cs_row["residual"] else "not measurable",
                            _conv_settle["tol_rel"])
                    if _cs_ok and (k + 1) < int(_vskip):
                        _cs_th = list(_sched_th[:k + 1])
                        _S = _build_schedule(int(_cs_p))
                        (_vskip, _v_nspp, _v_settle_periods, n_periods, n_total,
                         _dmskip, period_mech, dt, _fine_frames, _c_nspp,
                         _settle_bounds, _sched_mixed, _sched_th, _sched_dth,
                         _sched_dt, _sched_t, _sched_fine, _dc_win,
                         _vskip_periods, _progress_comp) = [
                            _S[_kk] for _kk in _SCHED_KEYS]
                        if (int(_vskip) != k + 1 or _sched_mixed or
                                not np.allclose(_sched_th[:k + 1], _cs_th,
                                                rtol=0.0, atol=1e-9)):
                            raise RuntimeError(
                                "converged settle: the rebuilt schedule is not "
                                "a prefix of the one marched (vskip %d, frame "
                                "%d) — refusing to splice" % (_vskip, k))
                        _fseq = _fseq[:_fi] + list(range(k + 1, int(n_total)))
                    # The reported window is known now: the incremental-Ldq
                    # probes are evenly spaced over it, and the animation
                    # keyframes span it, as the pre-loop selection does.
                    if inc_ldq:
                        _k0r = int(_vskip) + int(_dmskip)
                        _nrep = max(1, int(n_total) - _k0r)
                        _ns_inc = max(1, min(int(_INC_LDQ_SAMPLES), _nrep))
                        _inc_at = {_k0r + int(round(_j * _nrep / _ns_inc)) % _nrep
                                   for _j in range(_ns_inc)}
                    if int(return_frames) > 0 and n_total > 1:
                        _anim_k0 = int(_vskip) + int(_dmskip)
                        _nf = max(2, min(int(return_frames), n_total - _anim_k0))
                        _anim_idx = {_anim_k0 + int(round(
                            i * (n_total - 1 - _anim_k0) / (_nf - 1)))
                            for i in range(_nf)}
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
        if _gap_idx2.size:
            # AREA-weighted, not element-weighted: the gap is meshed finer near
            # the band than at the slot openings, and an unweighted mean would
            # be a mean of the mesh rather than of the field.
            _bg = np.hypot(_bx_el[_gap_idx2], _by_el[_gap_idx2])
            _wg = _ar_el[_gap_idx2]
            _bgap2.append(float((_bg * _wg).sum() / max(float(_wg.sum()), 1e-30)))
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
            # J (source) view: the APPLIED per-element source current density,
            # so the "J" view shows the winding currents at this rotor
            # position.  Same key and same construction as the P1 snapshot —
            # a viewer must not need a per-order case.  ALWAYS written, eddy
            # run or not — an eddy transient snapshot used to skip this
            # branch entirely (only `else` wrote it), so the "J" view served
            # from an eddy run's snapshot read all-zero (2026-09-20 bug: "J —
            # Source current density" showed max 0.0 · min 0.0, no +/-).
            _Js2 = np.zeros(int(mesh_all.t.shape[1]))
            for _ix, _ar, _dir, _ph, _as in coil_info:
                # Same divisor as the assembled source (the slot's REAL
                # copper area), so the J card reads the density the solve
                # actually used, not a nominal-rectangle one.
                _Js2[_ix] = (_dir * Ist[_ph] * n_wires / max(_as, 1e-12))
            _snap2["Jtri_src"] = _Js2
            if eddy:
                # J⟳ (eddy) view: the eddy current density J = σ(−∂A/∂t + U_b)
                # the coupled solve produces, sampled at the mesh VERTICES
                # (the P1 snapshot is nodal too, so the viewer needs no new
                # case).  Written IN ADDITION TO Jtri_src above, not instead
                # of it — a snapshot from an eddy run must still carry the
                # source density for the "J" view.
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
                    _tgs_i = _c.get("tags", [_c["tag"]])
                    for _tg_i, _sg_i in zip(_tgs_i, _c.get(
                            "signs", [1.0] * len(_tgs_i))):
                        _u_n[_bdofs(_elm[_tg_i])] = float(_Uev[_ci]) * _sg_i
                _snap2["Jeddy"] = (_sig_n * (-_dAe + _u_n))[vdof].copy()
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
    # …and, since 2026-09-05, the demag PRE-PASS: one more electrical period at
    # θ<0 on an eddy+demag run, solved and discarded so the Br ratchet only ever
    # sees the settled state (the user's two-identical-runs-disagree bug).
    _n_solved = int(n_total) + int(_n_warm) + int(_n_dmpre)
    # ── DEMAG SETTLE of the REPORTED window (shared rule, both methods) ──────
    # How far Br moved while the reported frames were solved.  Above the
    # tolerance the window is a demag TRANSIENT, not a steady state, and the
    # result says so (demag_settled False, steady_state False).
    _dm_settle: Optional[Dict[str, Any]] = None
    if demag and _dmst is not None and _dm_rep_br0 is not None:
        _dm_settle = dict(_dm_change(_dm_rep_br0, _br_glob))
        # the reported period is judged by the SAME three rules as a pre-pass
        # period (Br change; image history; observable drift of the last two
        # pre-pass periods) — all three affirmatively
        _dm_moved_ok = _dm_is_settled(_dm_settle)
        try:
            if _dm_shortcut_used:
                # the shortcut carries the worst magnet's history to EVERY pole
                # image by the symmetry map: complete when every map was
                _dm_img_ok = bool(_dm_passes) and all(
                    (_p_.get("map") or {}).get("complete") for _p_ in _dm_passes)
                _dm_img_rec = {"method": "pole-image transfer (demag shortcut)",
                               "complete": _dm_img_ok, "windows": len(_dm_passes)}
            else:
                _dm_img_rec, _dm_img_ok = _dm_image_check(len(_dm_passes))
        except Exception as _e_img:          # noqa: BLE001 — unverifiable: not steady
            _dm_img_rec, _dm_img_ok = {"error": str(_e_img)}, False
        _dm_drift_rec = _dm_drift(_dm_passes)
        _dm_settle.update({
            "tol": float(_dm_settle_tol),
            "element_tol": float(_dm_settle_etol),
            "moved_settled": bool(_dm_moved_ok),
            "image_history": _dm_img_rec,
            "drift": _dm_drift_rec,
            "settled": bool(_dm_moved_ok and _dm_img_ok and _dm_drift_rec["ok"]),
            # per element: a WARNING only (owner 2026-10-04), for the reported
            # period or the last pre-pass period
            "warning": (_demag_element_warning(_dm_settle, _dm_settle_etol)
                        or (_dm_passes[-1].get("element_warning")
                            if _dm_passes else None)),
            "prepass_periods": len(_dm_passes),
            "prepass_periods_max": int(_dm_cap()),
            "prepass": [{_kk: (float("%.4g" % _vv) if isinstance(_vv, float) else _vv)
                         for _kk, _vv in _p_.items()} for _p_ in _dm_passes]})
        if not _dm_settle["settled"]:
            _why = []
            if not _dm_moved_ok:
                _why.append(
                    "the reported period moved Br by %.3g (worst magnet, area mean "
                    "|dBr|/Br0; tol %.1g)"
                    % (_dm_settle["per_magnet_mean_max"], _dm_settle_tol))
            if not _dm_img_ok:
                _g = (_dm_img_rec or {}).get("image_gap") or {}
                _why.append(
                    "the rotor-image history is incomplete (%d of %s pre-pass "
                    "periods; Br above its image minimum by %s area mean, %s at "
                    "one element)%s" % (
                        len(_dm_passes), (_dm_img_rec or {}).get("cycle_periods"),
                        ("%.3g" % _g["per_magnet_mean_max"]) if _g else "?",
                        ("%.3g" % _g["element_max"]) if _g else "?",
                        (" — %s" % _dm_img_rec["error"])
                        if (_dm_img_rec or {}).get("error") else ""))
            if not _dm_drift_rec["ok"]:
                _why.append(
                    "the observables still drift between the last two pre-pass "
                    "periods (%s)" % (
                        _dm_drift_rec.get("why") or "torque %.3g (tol %.1g), ripple "
                        "%.3g pp (tol %.3g)" % (
                            _dm_drift_rec["T_mean_rel"], _dm_drift_rec["tol_T_rel"],
                            _dm_drift_rec["ripple_pp"], _dm_drift_rec["tol_ripple_pp"])))
            _dm_settle["note"] = (
                "demag NOT settled after %d pre-pass period(s): %s — a demag "
                "transient, not a steady state" % (len(_dm_passes), "; ".join(_why)))
            log.warning("P2 %s", _dm_settle["note"])
        if _dm_settle.get("warning"):
            log.warning("P2 demag WARNING: %s", _dm_settle["warning"])
    if _tdm_info is not None and _eddy_method == "tdm":   # a TDM that succeeded
        _tdm_info["t"]["report_loop"] = _t.time() - _tdm_info.pop("t_report_start")
        _tdm_info["report_newton_iterations_mean"] = (
            float(np.mean(_pic_iters)) if _pic_iters else None)
        # ── THE REPORT GATE (Codex reviews 2026-09-30 and 2026-10-03) ────────
        # The reported window — marched by the unchanged frame loop from the
        # orbit, the demag ratchet active exactly as in the march — must stay
        # on the orbit, judged for EVERY reported period (every orbit period
        # inside it: both halves of a half-period orbit) on
        #   * the STATE: per conductor group, BOTH BDF2 history levels at the
        #     end of the period, relative sigma-norm <= REPORT_GATE["state_rel"]
        #     (was the diagnostic `march_vs_orbit_*`, whose errors were
        #     swallowed; a slow-body drift, a wrong splice, history or map
        #     shows here), and
        #   * the owner's observables: mean torque, ripple and every conductor
        #     group's loss (REPORT_GATE; time_periodic says how they tie to the
        #     owner's terms).
        # The thresholds are constants (no environment knob loosens them).  ANY
        # error while computing the gate fails it.  A failure rejects this
        # solve: fem_transient_sliding_band retries once with the strict
        # state-residual stop on the full period, then marches.  The one
        # exception: a window in which Br is still moving (demag NOT settled by
        # the shared rule) is a demag transient, not a TDM defect — it is kept,
        # labelled steady_state False, and only when the failure is a threshold
        # (never an error).
        _gate = {"ok": None}
        try:
            _tdN = len(_tdm_orbit)
            _ks_o = list(range(_tdN))
            _o_obs = _td_observe(_tdm_orbit, _tdm_orbit_U, _td_orbit(-1, _tdm_orbit),
                                 _td_orbit(-2, _tdm_orbit), _ks_o)
            _gate = {"ok": True, "orbit": _o_obs, "windows": {}, "state": {},
                     "thresholds": dict(_tdm.REPORT_GATE)}
            _nrep = int(n_total)
            if _nrep % _tdN:
                raise RuntimeError("the reported window (%d frames) is not a whole "
                                   "number of orbit periods (%d)" % (_nrep, _tdN))
            for _k0 in range(0, _nrep, _tdN):
                _wn = "frames %d-%d" % (_k0, _k0 + _tdN - 1)
                _ks = list(range(_k0, _k0 + _tdN))
                _As_w = [_tdm_rep[_k][0] for _k in _ks]
                _Us_w = [_tdm_rep[_k][1] for _k in _ks]
                _h1 = (_tdm_rep[_k0 - 1][0] if _k0 - 1 >= 0 else _td_orbit(-1, _tdm_orbit))
                _h2 = (_tdm_rep[_k0 - 2][0] if _k0 - 2 >= 0 else _td_orbit(_k0 - 2, _tdm_orbit))
                _w_obs = _td_observe(_As_w, _Us_w, _h1, _h2, _ks)
                _ok_w, _cmp_w = _tdm.window_gate(
                    _o_obs, _w_obs,
                    **{_kk: _tdm.REPORT_GATE[_kk]
                       for _kk in ("T_rel", "ripple_pp", "P_rel", "P_floor_rel")})
                _cmp_w["observed"] = _w_obs
                _gate["windows"][_wn] = _cmp_w
                _ke = _k0 + _tdN
                _ok_s, _cmp_s = _tdm.state_closure(
                    [("frame %d" % _kl, _tdm_rep[_kl][0], _td_orbit(_kl, _tdm_orbit))
                     for _kl in (_ke - 1, _ke - 2)],
                    _Msig_grp, _tdm.REPORT_GATE["state_rel"])
                _gate["state"][_wn] = _cmp_s
                _gate["ok"] = bool(_gate["ok"] and _ok_w and _ok_s)
            # the old diagnostic names, now read off the hard gate
            _g_last = _gate["state"]["frames %d-%d" % (_nrep - _tdN, _nrep - 1)]
            _tdm_info["march_vs_orbit_by_group"] = dict(
                _g_last["levels"]["frame %d" % (_nrep - 1)])
            _tdm_info["march_vs_orbit_last_frame"] = max(
                _tdm_info["march_vs_orbit_by_group"].values())
            if _td_fault == "gate":
                _gate["ok"] = False
                _gate["injected"] = True
        except Exception as _e_g:            # noqa: BLE001 — an unverifiable run fails
            _gate = {"ok": False, "error": "%s: %s" % (type(_e_g).__name__, _e_g)}
        _tdm_info["gate"] = _gate
        log.info("TDM report gate: %s %s; state worst %s",
                 "PASSED" if _gate["ok"] else "FAILED",
                 {_wn: {"T_mean_rel": "%.2g" % _w["T_mean_rel"],
                        "ripple_pp": "%.2g" % _w["ripple_pp"],
                        "P_rel": {_g: "%.2g" % _v["rel"] for _g, _v in _w["P"].items()}}
                  for _wn, _w in (_gate.get("windows") or {}).items()},
                 {_wn: "%.2g" % _s["worst"]
                  for _wn, _s in (_gate.get("state") or {}).items()})
        if not _gate["ok"]:
            if ("error" not in _gate and _dm_settle is not None
                    and not _dm_settle.get("moved_settled", _dm_settle["settled"])):
                _gate["kept_because"] = "demag not settled: the window is a Br transient"
            else:
                raise TdmAttemptFailed(
                    "report_gate",
                    "the reported period does not reproduce the orbit (%s)"
                    % (_gate.get("error") or {
                        _wn: "T %.2g, ripple %.2g pp, P %s, state %.2g" % (
                            _w["T_mean_rel"], _w["ripple_pp"],
                            ",".join("%s %.2g" % (_g, _v["rel"])
                                     for _g, _v in _w["P"].items() if not _v["ok"]),
                            (_gate.get("state") or {}).get(_wn, {}).get(
                                "worst", float("nan")))
                        for _wn, _w in (_gate.get("windows") or {}).items()}),
                    info=_tdm_info,
                    retry_residual=bool(_td_stop != "residual"),
                    retry_full=bool(_td_neg))
    # ── CONVERGED SETTLE: what the REPORTED period moved against the last
    # settling one, measured the same way the criterion measured — the honest
    # final residual, including any drift the criterion's last pair missed.
    _voltage_settle = None
    if _conv_settle is not None:
        _voltage_settle = {
            _kk: _vv for _kk, _vv in _conv_settle.items()
            if not _kk.startswith("_") and _kk != "history"}
        _voltage_settle["history"] = [
            {_kk: _vv for _kk, _vv in _r.items() if _kk != "_m"}
            for _r in _conv_settle["history"]]
        _voltage_settle["reported_vs_last_settle"] = None
        _nw = int(_v_nspp)
        _o = len(_IA) - int(n_total)
        _r0 = _o + int(_vskip)
        if _nw > 0 and _r0 - _nw >= 0 and _r0 + _nw <= len(_IA):
            _ms = _settle_period_metrics(
                _T2[_r0 - _nw:_r0], _IA[_r0 - _nw:_r0], _IB[_r0 - _nw:_r0],
                _IC[_r0 - _nw:_r0], _dt_steps[_r0 - _nw:_r0])
            _mr = _settle_period_metrics(
                _T2[_r0:_r0 + _nw], _IA[_r0:_r0 + _nw], _IB[_r0:_r0 + _nw],
                _IC[_r0:_r0 + _nw], _dt_steps[_r0:_r0 + _nw])
            _voltage_settle["reported_vs_last_settle"] = {
                _kk: float(_vv)
                for _kk, _vv in _settle_period_residual(_ms, _mr).items()}

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
    # EVERY per-frame series the loop appends at k >= 0 belongs here (the
    # sb_postproc docstring says why; tests/test_solver_guards.py enumerates
    # the loop's appends against this tuple).  The sleeve loss `_ed_sl` and the
    # air-gap |B| `_bgap2` were missing until 2026-09-23: on every voltage /
    # PWM eddy run P_sleeve was averaged over the settling frames too, and the
    # loss-total zip paired the trimmed copper series with the sleeve's
    # SETTLING prefix (docs/solver-guards-2026-09-23.md).
    _v2_lists = (_T2, _T_vw, _T_vw_reason, _Tc2, _Tc2l,
                 _psiA, _psiB, _psiC, _IA, _IB, _IC, _tt, _theta_samples,
                 _hsx2, _hsy2, _hrx2, _hry2, _hcx2, _hcy2, _hmx2, _hmy2,
                 _histA_rot2, _pic_iters, _frame_converged, _bgap2,
                 _ed_cu, _ed_mag, _ed_sh, _ed_sl, _ed_dc2d, _ed_dens_hist,
                 _v_diag["iters"], _v_diag["resid"],
                 _vapp['A'], _vapp['B'], _vapp['C'], _dt_steps)
    # The frame solved immediately before the REPORTED window, captured before
    # the prefixes are dropped: the first reported step voltage is measured
    # against it (see _step_voltage_series).  The settling prefix is later in
    # time than any eddy warm-up, so it wins when both exist.
    _n_prefix = ((int(_vskip) if (_vdrive and _vskip) else 0)
                 + (int(_dmskip) if (_dmskip and demag and _dmst is not None)
                    else 0))
    if _n_prefix > 0 and len(_psiA) > _n_prefix:
        _jp = _n_prefix - 1
        _pre_frame = {"psi": (_psiA[_jp], _psiB[_jp], _psiC[_jp]),
                      "I": (_IA[_jp], _IB[_jp], _IC[_jp])}
    # Keep the compact scalar traces that the settle trims are about to erase.
    # The per-frame element/DOF arrays below remain un-copied; preserving their
    # discarded prefixes would duplicate the largest histories in the result.
    _p2_scalar_series = {
        "time_s_absolute": _tt,
        "mechanical_angle_rad": _theta_samples,
        "torque_em_Nm": _T2,
        "torque_virtual_work_diagnostic_Nm": _T_vw,
        "torque_coulomb_Nm": _Tc2,
        "psi_A_Wb": _psiA, "psi_B_Wb": _psiB, "psi_C_Wb": _psiC,
        "current_A_A": _IA, "current_B_A": _IB, "current_C_A": _IC,
        "picard_iterations": _pic_iters,
        "air_gap_B_mean_T": _bgap2,
        "eddy_copper_power_W": _ed_cu, "eddy_magnet_power_W": _ed_mag,
        "eddy_shaft_power_W": _ed_sh, "eddy_sleeve_power_W": _ed_sl,
        "eddy_dc_copper_power_W": _ed_dc2d,
        "voltage_solver_iterations": _v_diag["iters"],
        "voltage_solver_residual_V": _v_diag["resid"],
        "applied_voltage_A_V": _vapp['A'], "applied_voltage_B_V": _vapp['B'],
        "applied_voltage_C_V": _vapp['C'],
    }
    _p2_trim_operations = []
    if _vdrive and _vskip:
        _p2_trim_operations.append({"kind": "voltage_settling", "requested_frames": int(_vskip)})
    if _dmskip and demag and _dmst is not None:
        _p2_trim_operations.append({"kind": "demag_settling", "requested_frames": int(_dmskip)})
    # A bookkeeping copy of a finished solve must never be what fails it: the
    # snapshot skips a vector channel by itself (named in `skipped_series`),
    # and anything else it trips on is logged and leaves the history absent.
    _p2_raw_scalar_history = None
    if _p2_trim_operations:
        try:
            _p2_raw_scalar_history = _snapshot_scalar_history(_p2_scalar_series)
            if _p2_raw_scalar_history.get("skipped_series"):
                log.warning("P2 scalar history: %d channel(s) skipped as "
                            "non-scalar: %s",
                            len(_p2_raw_scalar_history["skipped_series"]),
                            _p2_raw_scalar_history["skipped_series"])
        except Exception as _e_hist:      # noqa: BLE001 — the solve is done
            log.warning("P2 scalar history not kept (%s: %s)",
                        type(_e_hist).__name__, _e_hist)
            _p2_raw_scalar_history = None
    _p2_field_history_counts = {
        "stator_Bx": len(_hsx2), "stator_By": len(_hsy2),
        "rotor_Bx": len(_hrx2), "rotor_By": len(_hry2),
        "coil_Bx": len(_hcx2), "coil_By": len(_hcy2),
        "magnet_Bx": len(_hmx2), "magnet_By": len(_hmy2),
        "rotor_potential_A": len(_histA_rot2),
        "eddy_loss_density": len(_ed_dens_hist),
    }
    # ── DC RESIDUAL OF THE REPORTED PERIOD (B5 / PWM study 2026-09-13) ────
    # Measured HERE, before the settling frames are dropped, because the
    # trapezoidal mean needs the frame BEFORE the window — and measured the way
    # the anchor measures (see _period_dc), so the gauge and the fix are the
    # same quantity.  The old gauge was the plain mean of phase A over the whole
    # reported window: one phase of three whose DCs sum to zero (3 A printed
    # while another carried 17), over a window that need not be a whole number
    # of electrical periods.  Now: LAST WHOLE electrical period, WORST phase.
    _dc_res = _dc_res_ph = None
    if _vdrive and _IA and _v_nspp and len(_IA) >= int(_v_nspp) + 1:
        _nw = int(_v_nspp)
        _i1 = len(_IA) - 1
        _wdc = np.asarray(_sched_dt[-_nw:], float)
        _dcs = {_ph: _period_dc(_s, _i1 - _nw + 1, _i1, _wdc)
                for _ph, _s in (('A', _IA), ('B', _IB), ('C', _IC))}
        _dc_res_ph = max(_dcs, key=lambda _p: abs(_dcs[_p]))
        _dc_res = float(_dcs[_dc_res_ph])
        if abs(_dc_res) > _V_DC_RESIDUAL_TOL_A:
            log.warning(
                "P2 vdrive: %.2f A of DC left in phase %s over the reported "
                "electrical period (tol %.2f A).  The reported TORQUE RIPPLE "
                "and current ripple are that DC, not the machine — the loss "
                "terms are barely touched by it.  DC-orbit solve: %s.",
                _dc_res, _dc_res_ph, _V_DC_RESIDUAL_TOL_A,
                ("off for this run" if _dc_orbit is None else
                 "corrections off (SB_V_DC_SOLVE=0)"
                 if not _dc_orbit.correcting else
                 "%d correction(s), %d refused"
                 % (_dc_orbit.corrections, _dc_orbit.refused)))
    # DEBUG DUMP of every solved frame INCLUDING the settle prefix and the eddy
    # warm-up (SB_DEBUG_DUMP_FRAMES=<path>) — the trimmed payload cannot show
    # where a DC current is born.  Read-only diagnostics; off by default.
    _dump_p = _os_sb.environ.get("SB_DEBUG_DUMP_FRAMES")
    if _dump_p:
        try:
            import json as _json_d
            # ONE PATH, MANY TRANSIENTS: a route call solves the harm_ref
            # reference (and, on some paths, a no-load run) AFTER the one being
            # studied, and each of them used to overwrite this file — so the
            # dump you opened was whichever transient finished last, which on a
            # PWM run is the sinusoidal reference with no settle at all.  Number
            # them instead.  (B5 / PWM study 2026-09-13.)
            global _DUMP_SEQ
            _DUMP_SEQ += 1
            if _DUMP_SEQ > 1:
                _rt, _ex = _os_sb.path.splitext(_dump_p)
                _dump_p = "%s.%d%s" % (_rt, _DUMP_SEQ, _ex)
            _fl = lambda a: [float(x) for x in a]  # noqa: E731
            _json_d.dump({
                "n_warm": len(_IA) - len(_sched_th), "vskip": int(_vskip),
                "fine_frames": int(_fine_frames), "c_nspp": int(_c_nspp),
                "v_nspp": int(_v_nspp), "mixed": bool(_sched_mixed),
                "period_mech": float(period_mech),
                "th": _fl(_sched_th), "dth": _fl(_sched_dth),
                "fine": [bool(x) for x in _sched_fine],
                "IA": _fl(_IA), "IB": _fl(_IB), "IC": _fl(_IC),
                "psiA": _fl(_psiA), "psiB": _fl(_psiB), "psiC": _fl(_psiC),
                "vA": _fl(_vapp['A']), "vB": _fl(_vapp['B']), "vC": _fl(_vapp['C']),
                "T": _fl(_T2),
            }, open(_dump_p, "w"))
            log.info("P2 debug frame dump written: %s (%d frames)", _dump_p, len(_IA))
        except Exception as _e_d:
            log.warning("P2 debug frame dump failed: %r", _e_d)
    if _vdrive and _vskip:
        n_total -= _vskip
        # _vskip_periods, not _v_settle_periods: under the mixed-resolution
        # schedule the prefix ALSO carries the demag settling period and the
        # fine pre-roll (the pre-roll is carved out of the last settle period,
        # so the prefix is still a whole number of electrical periods and the
        # reported window still starts at theta = 0).
        n_periods = float(n_periods) - float(_vskip_periods)
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

    _p2_transient_history = None
    if _p2_raw_scalar_history is not None:
        try:
            _p2_transient_history = dict(_p2_raw_scalar_history)
            _p2_transient_history["retained_window"] = _retained_window_metadata(
                _p2_raw_scalar_history, _p2_scalar_series,
                core_series=("time_s_absolute", "mechanical_angle_rad",
                             "torque_em_Nm", "psi_A_Wb", "psi_B_Wb", "psi_C_Wb",
                             "current_A_A", "current_B_A", "current_C_A"),
                trim_operations=_p2_trim_operations,
                nominal_retained_frames=n_total, retained_periods=n_periods)
            _p2_transient_history["non_scalar_history_sample_counts"] = {
                "before_trim": _p2_field_history_counts,
                "after_trim": {
                    "stator_Bx": len(_hsx2), "stator_By": len(_hsy2),
                    "rotor_Bx": len(_hrx2), "rotor_By": len(_hry2),
                    "coil_Bx": len(_hcx2), "coil_By": len(_hcy2),
                    "magnet_Bx": len(_hmx2), "magnet_By": len(_hmy2),
                    "rotor_potential_A": len(_histA_rot2),
                    "eddy_loss_density": len(_ed_dens_hist),
                },
                "values_copied": False,
            }
        except Exception as _e_hist:      # noqa: BLE001 — metadata, not physics
            log.warning("P2 transient history metadata not built (%s: %s)",
                        type(_e_hist).__name__, _e_hist)
            _p2_transient_history = None

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
    #
    # custom_current takes the SAME route, for the same reason one step earlier:
    # the imposed waveform's rms is not `I_phase_rms` either (that argument is
    # ignored entirely by this source), and a PWM waveform's rms sits measurably
    # above its fundamental — which is precisely the copper-loss effect the
    # study is trying to measure.  Reading it off the config would have thrown
    # that away and reported the sinusoid's I²R.
    _I_ph_rms_solved = None      # None = the current was IMPOSED, not solved
    if (_vdrive or _rms_from_series) and _IA:
        _P_cu_old = float(P_cu)
        P_cu, _k_end_used, _R_solved, _I_ph_solved = _vdrive_copper_loss(
            p, geo, _IA, _IB, _IC, n_parallel, coil_temp_c,
            end_winding_factor, copper_area_m2=_cu_area_m2)
        # The rms TERMINAL phase current this run actually carried.  Under an
        # imposed voltage the current is the answer, so `I_phase_rms` in the
        # request means nothing here — and every constraint an outer loop wants
        # to hold ("stay under the duty's current") is about THIS number.  It
        # was computed and thrown away; now it ships.
        _I_ph_rms_solved = float(_I_ph_solved)
        log.info("P2 %s copper: I_phase_solved=%.2f A rms (config %.2f) "
                 "-> P_cuDC %.1f -> %.1f W (R_phase=%.6g ohm)",
                 _drv_name, _I_ph_solved, float(I_phase_rms), _P_cu_old,
                 float(P_cu), _R_solved)

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
    P_cu_dc2 = 0.0 if "slot" in _ex_parts else float(P_cu)   # copper_loss_W (I²R)
    P_cu_ac_ser2 = [0.0] * n_total; P_cu_ac_avg2 = 0.0
    P_cu_ser2 = [P_cu_dc2] * n_total
    P_fe_ser2 = [0.0] * n_total; P_fe_avg2 = 0.0
    P_mag_ser2 = [0.0] * n_total; P_mag_avg2 = 0.0
    P_shaft_ser2 = [0.0] * n_total; P_shaft_avg2 = 0.0
    # The retaining sleeve's eddy loss.  It has ONE route only — the coupled
    # σ·∂A/∂t solve.  The frequency-domain `honest_rotor_eddy` path takes a
    # single "shaft tag" and models magnets + shaft; rather than pretend it
    # covers a third body, the sleeve simply reports 0 W when the coupled solve
    # did not run, which is what "not solved" has to look like here.
    P_sleeve_ser2 = [0.0] * n_total; P_sleeve_avg2 = 0.0
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

    def _iron_p2(hx, hy, idx, areas_half, mat, terms=None, periods=None,
                 closed=False):
        # n_periods: the DFT behind the measured-surface path needs to know how
        # many electrical periods the captured window spans, or it puts every
        # harmonic at the wrong frequency.  n_total/n_periods are the TRIMMED
        # values here — the voltage settling and demag prefixes were already
        # dropped above, and both were decremented with them.  The rotor's
        # commensurate window passes its own (longer) period count.
        return _iron_loss_series(
            hx, hy, idx, areas_half, mat, p.stack_length, f_elec,
            (len(hx) if hx is not None and len(hx) else n_total),
            _central_difference(dt), _mat_lib.effective_bertotti, terms=terms,
            n_periods=float(n_periods if periods is None else periods),
            closed_window=bool(closed))

    # STATOR: one electrical period is a closed window by construction.
    _pcl_s, _ph_s = _iron_p2(_hsx2, _hsy2, _iron_s_idx, areas_s, _steel_s,
                             _fe_terms_s, closed=True)
    # ROTOR: a COMMENSURATE window (simulation/rotor_window.py). One electrical
    # period is NOT closed for a rotor element (it slides a non-integer number
    # of slot pitches), so its DFT / periodic dB/dt / peak-to-peak read the
    # end-to-start step as broadband loss that grows with the step count. The
    # exact cure costs no solve: the rotor-frame history over q windows is the
    # solved window of the element's pole-pair images (stator-frame
    # periodicity + the pole-pair periodic rotor mesh). No ramp, no detrend,
    # no taper — the legacy detrended number survives only as a labelled
    # diagnostic of the OLD open window, never selected, never a fallback.
    _rw = {"closed": False, "q": 1, "method": "open_one_period_window",
           "reason": "no rotor iron history"}
    if _iron_r_idx.size and _hrx2:
        try:
            _rmsh = half["r"]["mesh"]
            _rw = _commensurate_rotor_window(
                _hrx2, _hry2, _rmsh.p[:, _rmsh.t].mean(axis=1), _iron_r_idx,
                areas_r, pole_pairs=int(pole_pairs), n_sectors=int(NS),
                bc_sign=int(_bc_sign), theta_rad=_theta_samples,
                window_periods=float(n_periods))
        except Exception as _rw_e:      # noqa: BLE001 — fall back, loudly
            _rw = {"closed": False, "q": 1,
                   "method": "open_one_period_window",
                   "reason": "commensurate window failed: %s: %s"
                             % (type(_rw_e).__name__, _rw_e)}
    _fe_terms_r_open: dict = {}
    if _rw.get("closed"):
        _q_rw = int(_rw["q"])
        _pcl_r_long, _ph_r = _iron_p2(
            _rw["X"], _rw["Y"], _iron_r_idx, areas_r, _steel_r, _fe_terms_r,
            periods=float(_rw["window_periods_total"]), closed=True)
        _pcl_r_long = np.asarray(_pcl_r_long, float)
        # Per-frame series: the instantaneous rotor classical loss at stator-
        # frame time θ_j is the same sum over the whole rotor in each of the q
        # chained windows (the image map is a bijection of the iron), so the
        # q copies are averaged back onto the reported frames — exact, and it
        # replaces the open window's wrong wrap-around dB/dt at j = 0, N−1.
        _pcl_r = (_pcl_r_long.reshape(_q_rw, -1).mean(axis=0)
                  if _pcl_r_long.size == _q_rw * int(n_total)
                  else np.full(int(n_total), float(np.mean(_pcl_r_long))))
        # DIAGNOSTIC ONLY: the one-period open window as c582449 (raw) and
        # 68de0ca (legacy ramp-removed) billed it. Never selected. Skipped
        # when the captured window was already commensurate (nothing open).
        if _q_rw > 1:
            _pcl_r_open, _ph_r_open = _iron_p2(
                _hrx2, _hry2, _iron_r_idx, areas_r, _steel_r,
                _fe_terms_r_open, closed=False)
        else:
            _pcl_r_open, _ph_r_open = _pcl_r, _ph_r
        log.info("rotor iron | commensurate window: %s, %d x %d frames = %g "
                 "electrical periods (observed rotor period %s T_e) | %.4g W "
                 "vs open one-period window %.4g W (diagnostic)",
                 _rw["method"], _q_rw, int(n_total),
                 float(_rw["window_periods_total"]),
                 _rw.get("observed_period_electrical"),
                 (float(np.mean(_pcl_r)) + _ph_r) * NS,
                 (float(np.mean(_pcl_r_open)) + _ph_r_open) * NS)
    else:
        log.warning("rotor iron | commensurate window NOT available (%s) — "
                    "the raw one-period window is used (no filter); the rotor "
                    "loss carries open-window leakage that grows with the "
                    "step count", _rw.get("reason"))
        _pcl_r, _ph_r = _iron_p2(_hrx2, _hry2, _iron_r_idx, areas_r, _steel_r,
                                 _fe_terms_r, closed=False)
        _pcl_r_open, _ph_r_open = _pcl_r, _ph_r
        _fe_terms_r_open = _fe_terms_r
    _P_fe_rotor_window = {
        k: v for k, v in _rw.items() if k not in ("X", "Y")}
    # (No clamp at 0: every term is a sum of non-negative contributions, so the
    # np.maximum(…, 0) that sat here was inert — removed 2026-09-24 with the
    # other filter-like operations.)
    _P_fe_t = (_pcl_s + _pcl_r) * NS + (_ph_s + _ph_r) * NS
    P_fe_ser2 = _P_fe_t.tolist(); P_fe_avg2 = float(np.mean(_P_fe_t))
    # Per-term, per-half split of the iron loss (scaled to the whole machine).
    # Reported rather than re-derived: "the core loss looks low" is answerable
    # only by which TERM is low, and until now nothing downstream could see the
    # hysteresis/eddy/excess split or the k_f each half was billed at.
    _fe_break = {}
    for _half, _tm in (("stator", _fe_terms_s), ("rotor", _fe_terms_r)):
        if _tm:
            _fe_row = {
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
            for _candidate_key in (
                    "surface_raw_window_candidate_W",
                    "surface_detrended_candidate_W"):
                if (_candidate_key in _tm
                        and _tm[_candidate_key] is not None
                        and np.isfinite(_tm[_candidate_key])):
                    _fe_row[_candidate_key] = float(_tm[_candidate_key] * NS)
            if "surface_selected_candidate" in _tm:
                _fe_row["surface_selected_candidate"] = _tm[
                    "surface_selected_candidate"]
            _fe_row["window_closed"] = bool(_tm.get("window_closed", False))
            if _half == "rotor":
                _fe_row["window_electrical_periods"] = float(
                    _rw.get("window_periods_total", n_periods)
                    if _rw.get("closed") else n_periods)
                _fe_row["window_method"] = _rw.get("method")
            _fe_break[_half] = _fe_row
    # The OLD one-period rotor window, kept as a labelled DIAGNOSTIC (never
    # selected, never a fallback): its raw total (what c582449 selected) and,
    # for a measured-surface steel, its legacy ramp-removed total (what 68de0ca
    # selected). Machine totals (× sectors).
    _rot_open_raw_W = None
    _rot_open_legacy_W = None
    if _fe_terms_r_open:
        _rot_open_raw_W = float((float(np.mean(_pcl_r_open)) + _ph_r_open) * NS)
        if _fe_terms_r_open.get("surface_detrended_candidate_W") is not None:
            _rot_open_legacy_W = float(
                _fe_terms_r_open["surface_detrended_candidate_W"] * NS)
    _rot_sel_W = float((float(np.mean(_pcl_r)) + _ph_r) * NS) \
        if np.size(_pcl_r) else 0.0
    P_fe_rotor_open_window_diag = {
        "selected": False,
        "note": ("DIAGNOSTIC ONLY — the rotor iron on the one-period OPEN "
                 "window (not commensurate with slot passing). raw_W is what "
                 "c582449 selected, legacy_detrended_W what 68de0ca selected "
                 "(linear-ramp removal, a filter). Neither feeds any reported "
                 "number."),
        "raw_W": _rot_open_raw_W,
        "legacy_detrended_W": _rot_open_legacy_W,
        "legacy_wrap_guard_weight": (
            _fe_terms_r_open.get("legacy_wrap_guard_weight")
            if _fe_terms_r_open else None),
        "commensurate_selected_W": _rot_sel_W,
    }
    _has_surface_candidates = any(
        (_tm.get("surface_raw_window_candidate_W") is not None)
        for _tm in (_fe_terms_s, _fe_terms_r))
    if _has_surface_candidates:
        # P_fe_avg2 IS the unfiltered raw-window value (stator one period,
        # rotor commensurate). The "detrended" total is the 68de0ca-style
        # diagnostic: the same stator plus the rotor's legacy open-window
        # detrended value — None when the rotor has no such comparator.
        P_fe_raw_window_candidate_avg2 = float(P_fe_avg2)
        P_fe_detrended_candidate_avg2 = (
            None if _rot_open_legacy_W is None
            else float(P_fe_avg2 - _rot_sel_W + _rot_open_legacy_W))
    else:
        P_fe_raw_window_candidate_avg2 = None
        P_fe_detrended_candidate_avg2 = None
    if _fe_break:
        log.info("iron loss | %s | total %.2f W",
                 " | ".join("%s: hyst %.2f + eddy %.2f + excess %.2f = %.2f W "
                            "(k_f %.3f)"
                            % (_h, _v["hysteresis_W"], _v["eddy_W"],
                               _v["excess_W"],
                               _v["hysteresis_W"] + _v["eddy_W"] + _v["excess_W"],
                               _v["k_f"])
                            for _h, _v in _fe_break.items()), P_fe_avg2)
    # ── Publish the warm cache for the NEXT run ───────────────────────────
    # The seed field was stashed mid-march (k = n_total−3); the same-angle
    # solid-loss reference needs the WHOLE measured window, so it is built
    # here from the per-frame lists (same expression and _wsc scaling as the
    # settle gauge's samples).
    _wc_pending: List[Dict[str, Any]] = []   # published after the TDM error-estimate verdict
    if _wc_A is not None:
        try:
            _solid_ref = [
                ((_ed_mag[_i] + _ed_sh[_i])
                 if ("mag" in _Msig_grp or "shaft" in _Msig_grp)
                 else _ed_cu[_i]) for _i in range(len(_ed_cu))]
            # ── …and the Br RATCHET state beside it (user 2026-09-06) ─────
            # Published by EVERY coupled-eddy demag run, the interactive
            # Simulation included — that is what makes one Run at the base
            # point the "once" that seeds a whole sweep.  Publishing is
            # after-the-fact and changes no number in the run that publishes
            # it; only a run started with SB_SEED_FROM_PREVIOUS=1 ever reads
            # it back (see `_seed_from_previous`).
            # Stored as the pure DE-RATING factor (1.0 = pristine), with the
            # element centroids and the magnet domain tag that let another
            # mesh pick it up element by element.
            _br_pub = _br_cen = _br_tag = None
            if demag and _dmst is not None and _mag_idx.size:
                _rm_pub = half["r"]["mesh"]
                _cen_pub = np.asarray(_rm_pub.p, float)[
                    :, np.asarray(_rm_pub.t, int)].mean(axis=1).T
                _tg_pub = np.zeros(int(_br_glob.size), int)
                for _tg, _ix in half["r"]["cells"].items():
                    _tg_pub[np.asarray(_ix, int)] = int(_tg)
                _br_pub = (np.asarray(_br_glob, float)[_mag_idx]
                           / max(float(magnet_scale), 1e-12))
                _br_cen = _cen_pub[_mag_idx]
                _br_tag = _tg_pub[_mag_idx]
            if len(_solid_ref) == n_total:
                _wc_pending.append({
                    "doflocs": np.asarray(b2.doflocs.T,
                                          dtype=np.float32).copy(),
                    "A": _wc_A, "Ued": _wc_Ued,
                    "A2": _wc_A2,
                    "solid": np.asarray(_solid_ref, float),
                    # per conductor group, for the per-group same-angle test
                    "solid_grp": {_gk: np.asarray(_gl, float) for _gk, _gl in
                                  (("cu", _ed_cu), ("mag", _ed_mag),
                                   ("shaft", _ed_sh), ("sleeve", _ed_sl))
                                  if _gk in _warm_grp and len(_gl) == n_total},
                    "meta": dict(_wmeta),
                    "I": float(I_phase_rms), "rpm": float(rpm),
                    "gam": float(gamma_deg), "geo_fp": _geo_fp,
                    # The steps/period the CALLER asked for, beside the snapped
                    # one in `meta`.  The sweep coordinator only knows the
                    # request (the snap needs a built machine), so without this
                    # it compared 36 against a published 54 and concluded "no
                    # usable seed" on every eval of a sweep that was in fact
                    # seeding perfectly — quoting the cold price and running a
                    # pointless solo first point.  Never part of the seed key:
                    # what has to match is the SOLVED discretisation.
                    "nspp_req": int(_req_steps),
                    # The symmetry this state was solved on.  A full-ring state
                    # is not a seed for a 1/2-sector run (the eddy history has
                    # a different conductor set) - see _warm_seed_accept.
                    "nsect": (1 if int(n_sectors) <= 1 else int(n_sectors)),
                    "br_val": _br_pub, "br_cen": _br_cen, "br_tag": _br_tag,
                })
        except Exception:   # best-effort, never fails the run
            pass

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
            _mag_items_seg = [(int(_tg), np.asarray(_e, int))
                              for _tg, _e in half["r"]["cells"].items()
                              if int(_tg) >= DOM_MAG_BASE and np.size(_e)]
            _bodies_seg = [_pt_seg[:, np.unique(_tt_seg[:, _e])]
                           for _tg, _e in _mag_items_seg]
            # The loop width comes from the magnet's CAD OUTLINE — the polygon
            # the mesher tagged this body from — so it is a property of the
            # geometry, not of where Triangle put the nodes (2026-09-25; the
            # node cloud moved the reported loss −1 % under remeshing).  A body
            # is matched to the outline that CONTAINS its area centroid (the
            # tag index is tried first); a magnet cut by the sector boundary
            # therefore gets its WHOLE outline, which is the physical block.
            _polys_seg = []
            try:
                from motor_ai_sim.simulation.cut_bodies import (
                    match_outlines as _match_ol)
                _ol_seg = _match_ol(
                    _pt_seg, _tt_seg, [_e for _tg, _e in _mag_items_seg],
                    [mp for mp, _pl in (polys.get("magnets") or [])],
                    tags=[_tg for _tg, _e in _mag_items_seg],
                    tag_base=int(DOM_MAG_BASE))
                _polys_seg = [None if _g is None else
                              np.asarray(_g.exterior.coords, float).T * 1e-3
                              for _g in _ol_seg]
            except Exception as _e_pl:     # noqa: BLE001 — node fallback, said so
                log.warning("magnet segmentation: CAD outlines unavailable "
                            "(%s) — width from the mesh nodes", _e_pl)
                _polys_seg = []
            _seg_k, _seg_rep = _magnet_segmentation(
                geo, _bodies_seg, float(p.stack_length),
                polygons_by_body=_polys_seg)
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
    # The history is the COMMENSURATE rotor window (rotor_window.py), the same
    # construction as the rotor iron's but on the nodal potential: one
    # electrical period is open for a rotor node, and the frequency-domain
    # solve takes the DFT of every boundary node's history, so an open window
    # would bill its end-to-start step as broadband loss (that growth with the
    # step count is what the retired k ≤ 16 cap was hiding).
    _P_rot_eddy_window = {"closed": False, "q": 1,
                          "method": "open_one_period_window",
                          "reason": ("no rotor potential history"
                                     if not _histA_rot2 else None)}
    if _histA_rot2:
        try:
            _rm = half["r"]["mesh"]
            _hA2 = np.asarray(_histA_rot2, float)
            try:
                _rwA = _commensurate_rotor_potential_window(
                    _hA2, np.asarray(_rm.p, float),
                    float(np.sqrt(np.min(_triangle_areas(_rm)))),
                    pole_pairs=int(pole_pairs), n_sectors=int(NS),
                    bc_sign=int(_bc_sign), theta_rad=_theta_samples,
                    window_periods=float(n_periods))
            except Exception as _rwa_e:   # noqa: BLE001 — fall back, loudly
                _rwA = {"closed": False, "q": 1,
                        "method": "open_one_period_window",
                        "reason": "commensurate window failed: %s: %s"
                                  % (type(_rwa_e).__name__, _rwa_e)}
            if _rwA.get("closed"):
                _hA2 = np.asarray(_rwA["A"], float)
            else:
                log.warning("P2 honest rotor eddy | commensurate window NOT "
                            "available (%s) — the raw one-period window is used "
                            "(no filter); its open-window leakage grows with "
                            "the step count", _rwA.get("reason"))
            _P_rot_eddy_window = {k: v for k, v in _rwA.items() if k != "A"}
            # Tags + magnet list + mu lookup: shared with P1 (losses.py).
            _tags_r2, _magt2 = _rotor_eddy_tags(
                half["r"]["cells"], _rm.t.shape[1], DOM_MAG_BASE)
            import time as _time_hre
            _t_hre = _time_hre.time()
            # ── THE COUPLED SOLVE'S OWN TERMS (2026-09-25) ────────────────
            # This route used μ_r = 1 in the shaft (losses.rotor_mu_lookup:
            # "shaft aluminium"), ONE mean μ_r for the whole back iron, a
            # ∫J = 0 row on the half shaft and Neumann on the cut — none of
            # which is the machine the coupled solve models, and on the L155
            # it read 82 W of shaft loss against 3.9 W coupled
            # (docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md §5).  Now:
            # the window-mean per-element ν the solve converged (frozen
            # permeability), the (anti)periodic cut, U ≡ 0 on the cut ring,
            # a bisected magnet as ONE body, each body's own σ-mass.  Still a
            # LINEAR model: on a saturable shaft its shaft figure is a linear
            # estimate, not a check of the coupled value — said in the result.
            _nu_hre = (None if not _nu_rot_n
                       else np.asarray(_nu_rot_sum, float) / float(_nu_rot_n))
            if _nu_hre is None or _nu_hre.size != int(_rm.t.shape[1]):
                _nu_hre = np.asarray(nu_all2, float)[nst:]
            from motor_ai_sim.simulation.eddy_solver_2d import (
                rotor_eddy_solver_bc as _hre_bc)
            _mag_items_h = [(int(_t_h), np.asarray(_e_h, int))
                            for _t_h, _e_h in half["r"]["cells"].items()
                            if int(_t_h) in {int(x) for x in _magt2}]
            _pairs_h, _ = _bisected_magnet_pairs(_mag_items_h)
            P_mag_avg2, P_shaft_avg2, _hf2, _hre_info = _hre_bc(
                np.asarray(_rm.p, float), np.asarray(_rm.t, int), _tags_r2,
                _nu_hre, _sigma_of_tag, _magt2, DOM_SHAFT,
                _hA2, float(_hA2.shape[0]) * dt,
                float(p.stack_length), float(NS), int(NS), int(_bc_sign),
                mag_pairs=_pairs_h)
            # is the shaft a saturable (magnetic) conductor?  then its figure
            # here is a linear estimate only (no μ(B) modulation)
            _sh_mag = bool(half["r"]["cells"].get(int(DOM_SHAFT)) is not None
                           and np.size(half["r"]["cells"][int(DOM_SHAFT)])
                           and int(DOM_SHAFT) in sat_bh["r"])
            # …and the field reaching ANY shaft passes the saturating back
            # iron, whose μ(B) modulation makes rotor-frame harmonics a frozen
            # μ cannot: measured on the 30 mm fixture's aluminium shaft,
            # 0.001 W linear against 0.016 W coupled.  So the shaft figure is
            # a linear estimate whenever the iron in front of it saturates.
            _bi_sat = bool(int(DOM_ROTOR) in sat_bh["r"])
            _P_rot_eddy_window.update({
                "n_frames": int(_hA2.shape[0]),
                "n_harmonics_solved": len(_hf2),
                "model": ("frequency-domain LINEAR phasor solve on the coupled "
                          "solve's terms: window-mean per-element secant ν "
                          "(frozen permeability), (anti)periodic cut, U=0 cut "
                          "ring, bisected magnets as one body"),
                "constraints": _hre_info,
                "shaft_magnetic": _sh_mag,
                # the linear route omits the μ(B) modulation the coupled
                # solve carries (in a steel shaft, and in the back iron in
                # front of any shaft): its shaft figure is not a cross-check
                "shaft_is_linear_estimate": bool(_sh_mag or _bi_sat),
                "wall_s": round(_time_hre.time() - _t_hre, 2)})
            P_mag_ser2 = [float(P_mag_avg2)] * n_total
            P_shaft_ser2 = [float(P_shaft_avg2)] * n_total
            _lm2 = "field+honest (P2 magnetostatic + coupled rotor eddy)"
            log.info("P2 rotor eddy: mag=%.3f shaft=%.3f W (%d harmonics, "
                     "%s window x%d, %.1f s); iron=%.3f W, copper(dc)=%.1f W",
                     P_mag_avg2, P_shaft_avg2, len(_hf2),
                     _P_rot_eddy_window.get("method"),
                     int(_P_rot_eddy_window.get("q", 1)),
                     _time_hre.time() - _t_hre, P_fe_avg2, P_cu_dc2)
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
            if _ed_sl and "sleeve" in _Msig_grp:
                P_sleeve_ser2 = list(_ed_sl)
                P_sleeve_avg2 = float(np.mean(_ed_sl))
            log.info("EDDY-SOLVE(P2) magnet=%.3f shaft=%.3f sleeve=%.4f W vs "
                     "frequency-domain LINEAR route magnet=%.3f shaft=%.3f W%s",
                     P_mag_avg2, P_shaft_avg2, P_sleeve_avg2, P_mag_prox_avg2,
                     P_shaft_prox_avg2,
                     (" (shaft behind saturating iron: its linear figure is an "
                      "estimate, not a check — no μ(B) modulation)"
                      if _P_rot_eddy_window.get("shaft_is_linear_estimate")
                      else ""))
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
    P_tot_ser2 = [c + f + m + s + sl for c, f, m, s, sl in
                  zip(P_cu_ser2, P_fe_ser2, P_mag_ser2, P_shaft_ser2,
                      P_sleeve_ser2)]
    P_loss_avg2 = float(np.mean(P_tot_ser2)) if P_tot_ser2 else 0.0

    # ── THE EDDY SETTLE VERDICT, AFFIRMATIVE ONLY (third Codex review,
    # 2026-10-04).  `_warm_quiet` is True (measured, settled), False (measured,
    # not settled) or None — and None is NOT "settled": it is an unmeasured
    # voltage settle prefix or a PWM gauge that cannot judge.  Only a run with
    # no coupled-eddy march at all is settled by construction.
    if not eddy or _warm_quiet is True:
        _eddy_verdict, _eddy_verdict_note = True, None
    elif _warm_quiet is False:
        _eddy_verdict = False
        _eddy_verdict_note = ("eddy warm-up not settled (residual %s of the settled "
                              "solid loss, tol %s)"
                              % (None if _warm_resid is None else "%.3g" % _warm_resid,
                                 _EDDY_SETTLE_TOL))
    elif _conv_settle is not None and _conv_settle.get("converged") is True:
        _eddy_verdict, _eddy_verdict_note = True, None   # the converged settle passed
    else:
        _eddy_verdict = None
        _eddy_verdict_note = (
            "eddy settle UNKNOWN (%s) — not reported as a steady state" % (
                ("the PWM settle gauge cannot judge this schedule: residual %s is "
                 "an upper bound" % (None if _warm_resid is None
                                     else "%.3g" % _warm_resid))
                if (_vdrive and _carriers) else
                ("the voltage-drive settle prefix (%d frame(s)) was not measured"
                 % int(_vskip)) if _vdrive else "no settle measurement"))
        log.warning("P2 %s", _eddy_verdict_note)

    # ── TDM ORBIT-ERROR ESTIMATE: THE VERDICT (third / fourth Codex reviews) ──
    # The estimate mapped to the observables before the frame loop
    # (`tdm.orbit_error_estimate`) is judged here, where the total loss is
    # known: estimated torque / ripple / TOTAL-loss errors (the iron loss
    # computed directly) plus the reported period's own deviation from the
    # orbit must stay below ESTIMATE_SAFETY of the owner's terms, or the attempt
    # is rejected (one strict retry, then a march with a note).  A FIRST-ORDER
    # ESTIMATE with an empirical safeguard, not a proof.
    if (_tdm_info is not None and _eddy_method == "tdm"
            and isinstance(_tdm_info.get("orbit_error_estimate"), dict)):
        # a window in which Br still moves is a demag TRANSIENT, labelled
        # steady_state False whatever the orbit's accuracy: its deviation from
        # the orbit is the ratchet's, not an orbit error, so the estimate
        # judges the orbit alone there (and says so)
        _c_transient = bool(_dm_settle is not None
                            and not _dm_settle.get("moved_settled", True))
        _ok_c, _rec_c = _tdm.estimate_check(
            _tdm_info["orbit_error_estimate"], P_total_W=float(P_loss_avg2),
            report_windows=(None if _c_transient
                            else (_tdm_info.get("gate") or {}).get("windows")))
        if _c_transient:
            _rec_c["report_part_excluded"] = ("demag transient in the reported "
                                              "window (steady_state False)")
        if _td_fault == "estimate_gate":
            _ok_c = False
            _rec_c["ok"] = False
            _rec_c["injected"] = True
        _tdm_info["orbit_error_estimate"].update(_rec_c)
        log.info("TDM orbit-error estimate: %s — rho_eff %.4g, estimated error: "
                 "torque %.2g, ripple %.2g pp, total loss %.2g (limits %s)",
                 "ACCEPTED" if _ok_c else "NOT ACCEPTED",
                 float(_tdm_info["orbit_error_estimate"].get("rho_eff", float("nan"))),
                 float(_rec_c.get("estimate_T_rel", float("nan"))),
                 float(_rec_c.get("estimate_ripple_pp", float("nan"))),
                 float(_rec_c.get("estimate_P_total_rel", float("nan"))),
                 _rec_c.get("limits"))
        if not _ok_c:
            raise TdmAttemptFailed(
                "error_estimate",
                "the estimated orbit error exceeds %g of the owner's terms (rho_eff "
                "%.4g; torque %.3g, ripple %.3g pp, total loss %.3g)%s" % (
                    _tdm.ESTIMATE_SAFETY,
                    float(_tdm_info["orbit_error_estimate"].get("rho_eff", float("nan"))),
                    float(_rec_c.get("estimate_T_rel", float("nan"))),
                    float(_rec_c.get("estimate_ripple_pp", float("nan"))),
                    float(_rec_c.get("estimate_P_total_rel", float("nan"))),
                    (" — " + _tdm_info["orbit_error_estimate"]["why"])
                    if _tdm_info["orbit_error_estimate"].get("why") else ""),
                info=_tdm_info, retry_residual=bool(_td_stop != "residual"),
                retry_full=bool(_td_neg))
    # the warm cache is published only by a solve that is ACCEPTED (after the
    # orbit-error estimate verdict): a rejected TDM attempt must leave nothing for the retry
    # or the march to start from (transactional)
    for _wcp in _wc_pending:
        try:
            _warm_cache_store(_wcp)
        except Exception as _e_wcp:          # noqa: BLE001 — an accelerator only
            log.warning("P2 eddy warm cache not published (%s)", _e_wcp)
    _wc_pending = []

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
                hist_sx=_hsx2, hist_sy=_hsy2,
                # Rotor: the SAME commensurate window the watts came from.
                hist_rx=(_rw["X"] if _rw.get("closed") else _hrx2),
                hist_ry=(_rw["Y"] if _rw.get("closed") else _hry2),
                n_periods_rotor=(float(_rw["window_periods_total"])
                                 if _rw.get("closed") else None),
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
    # Raw Maxwell-stress (Arkkio) torque, retained as a diagnostic. Its accuracy
    # depends on the gap field and discretization. Historical bias measurements
    # do not establish a universal error for the current P2/source formulation.
    _T2raw = list(_T2)                       # preserve the Maxwell series (diag)
    # Raw Maxwell harmonic diagnostic over every returned sample. Bin orders
    # use the actual window duration and may be fractional electrical orders.
    # Retain every resolved order at full precision. An order alone does not
    # establish whether its amplitude is physical or a numerical artifact.
    T_harm_order, T_harm_amp = _torque_harmonics(
        _T2raw, n_steps_per_period,
        step_periods=float(_sched_dth[-1]) / period_mech)
    T_arr = np.asarray(_T2, float)
    T_maxwell_avg = float(T_arr.mean()) if T_arr.size else 0.0
    # Eligible all-bin terminal-work mean plus raw Maxwell AC. This is not a
    # general virtual-work certificate; see sb_postproc.hybrid_torque and
    # docs/solver-torque-validation-plan.md for the remaining error gates.
    _torque_method = "maxwell_stress"
    _retained_periods_integer = (
        math.isfinite(float(n_periods)) and float(n_periods) > 0.0
        and math.isclose(float(n_periods), round(float(n_periods)),
                         rel_tol=1e-10, abs_tol=1e-10))
    _torque_method_args = {
        "mechanical_angle_rad": _theta_samples,
        "imposed_current_drive": not bool(_vdrive),
        "eddy": bool(eddy),
        "rotor_eddy": bool(rotor_eddy),
        "demag": bool(demag),
        "frozen_nu": bool(frozen_nu),
        "all_frames_converged": (bool(_frame_converged)
                                 and all(_frame_converged)),
        "integer_period_window": bool(_retained_periods_integer),
    }
    # A source that can carry zero-sequence current (per-coil / open-winding,
    # simulation/per_coil.py) declares it; the flux-linkage mean then adds
    # the zero-sequence term the Clarke pair drops.  Every three-wire drive
    # leaves it False and is unchanged bit for bit.
    _zero_seq = bool(getattr(_src, "zero_sequence_path", False))
    try:
        _T2, _torque_method = _hybrid_torque(
            _psiA, _psiB, _psiC, _IA, _IB, _IC, _T2raw, pole_pairs,
            n_parallel=int(n_parallel), zero_sequence=_zero_seq,
            **_torque_method_args)
    except Exception as _te:
        # House rule: never fall to the Maxwell MEAN on a loaded run — the
        # flux-linkage mean (68de0ca) is the fallback, exactly as for every
        # run that is not terminal-work eligible.
        log.warning("P2 terminal-work torque failed (%s) — using the "
                    "flux-linkage (space-vector) mean", _te)
        try:
            _T2, _torque_method = _space_vector_hybrid_torque(
                _psiA, _psiB, _psiC, _IA, _IB, _IC, _T2raw, pole_pairs,
                n_parallel=int(n_parallel), zero_sequence=_zero_seq,
                mechanical_angle_rad=_theta_samples)
        except Exception as _te2:   # noqa: BLE001
            log.warning("P2 space-vector torque failed (%s) — using the raw "
                        "Maxwell series", _te2)
            _T2, _torque_method = list(_T2raw), "maxwell_stress"
    _torque_mean_source = _TORQUE_MEAN_SOURCE.get(_torque_method, "raw_maxwell")
    try:
        _torque_method_diag = _torque_method_diagnostics(
            _psiA, _psiB, _psiC, _IA, _IB, _IC, _T2raw, pole_pairs,
            n_parallel=int(n_parallel), selected_method=_torque_method,
            t_coulomb=_Tc2, **_torque_method_args)
    except Exception as _diag_error:
        # Diagnostics are additive and must never interrupt a completed solve.
        _torque_method_diag = {
            "validation_status": "uncertified",
            "validation_reason": "available solver metadata do not certify a general torque method",
            "candidate_kind": "fundamental_space_vector_mean_candidate",
            "selected_method": str(_torque_method),
            "space_vector_mean_candidate_Nm": None,
            "raw_maxwell_mean_Nm": None,
            "space_vector_minus_maxwell_mean_Nm": None,
            "terminal_work_mean_candidate_Nm": None,
            "terminal_work_minus_maxwell_mean_Nm": None,
            "terminal_work_method_eligible": False,
            "terminal_work_eligibility_reason":
                "diagnostic evaluation failed: " + type(_diag_error).__name__,
            "per_branch_peak_current_A": None,
            "legacy_selector_would_use_space_vector_mean": None,
            "certified_energy_balance_Nm": None,
            "diagnostic_input_reason": "diagnostic evaluation failed: "
                                       + type(_diag_error).__name__,
        }
    # Coulomb virtual work (simulation/virtual_work_torque.py), stored on every
    # run; torque_method="coulomb" makes it the reported waveform/mean/ripple.
    _coul2 = _coulomb_series_summary(
        _Tc2, _Tc2l, _ftq2.unavailable_reason, n_steps_per_period=n_steps_per_period,
        step_periods=float(_sched_dth[-1]) / period_mech)
    _coul2["layers"] = _ftq2.layers_info
    _coul2["torque_method_requested"] = torque_method
    if _coul2["layer_self_check"].get("ripple_mesh_limited"):
        log.warning("Coulomb layer self-check: rotor- and stator-side gap rings "
                    "differ by %.3g N·m = %.1f %% of the ripple scale at %g gap "
                    "layers/side — the ripple is limited by the air-gap mesh",
                    _coul2["layer_self_check"]["max_abs_diff_Nm"],
                    100.0 * _coul2["layer_self_check"]["rel_to_ripple_scale"],
                    float(gap_layers))
    if torque_method == "coulomb":
        if _coul2["available"]:
            _T2 = list(_coul2["T_coulomb_series"])
            _torque_method = _torque_mean_source = "coulomb_virtual_work"
            T_harm_order = _coul2["T_harm_order_coulomb"]
            T_harm_amp = _coul2["T_harm_amp_coulomb"]
        else:
            log.warning("torque_method='coulomb' requested but Coulomb torque "
                        "is unavailable (%s); reporting %s",
                        _coul2["unavailable_reason"], _torque_method)
    T_arr = np.asarray(_T2, float)
    Tavg = float(T_arr.mean()) if T_arr.size else 0.0
    _T_report, Trip_raw = torque_metrics(_T2)
    T_ripple_pp = (float(np.ptp(np.asarray(_T2, float))) if _T2 else 0.0)
    _omega_m2 = 2.0 * math.pi * rpm / 60.0
    P_airgap_avg2 = float(Tavg * _omega_m2)
    P_mech_avg2 = P_airgap_avg2 - (P_fe_avg2 + P_mag_avg2 + P_shaft_avg2
                                   + P_sleeve_avg2)
    # Terminal (winding) voltage V = R·i + dψ/dt, per STEP, exactly as the
    # time integration sees it (`_step_voltage_series`): the Crank–Nicolson
    # circuit row R·(i_k + i_{k−1})/2 + (ψ_k − ψ_{k−1})/Δt_k, i.e. the EXACT
    # step-mean EMF plus the resistive drop at the step-mean current.  On a
    # voltage run it is the applied voltage to the Newton residual; on a
    # current run it is what a CN voltage drive would apply to reproduce the
    # solved currents.  No harmonic is truncated (it replaced a spectral
    # derivative cut above the second slot harmonic — owner 2026-09-24).
    # Periodic by construction on an imposed-current window (the predecessor
    # of frame 0 is frame N−1); a run with a solved settling prefix / eddy
    # warm-up measures frame 0 against the frame it actually followed.
    # ψ and I are per-branch (sc_psi = L·NS/n_par).  Each voltage sample sits
    # at its step's MIDPOINT: `V_rotor_angle_deg` carries those angles.
    _dts_v = (np.asarray(_dt_steps, float) if len(_dt_steps) == len(_psiA)
              else np.full(len(_psiA), float(dt)))

    def _vser(psi, cur, j):
        _pv = (None if _pre_frame is None
               else (_pre_frame["psi"][j], _pre_frame["I"][j]))
        return _step_voltage_series(psi, cur, R_phase, _dts_v, _pv)
    if _psiA:
        (_vA, _iAm), (_vB, _iBm), (_vC, _iCm) = (
            _vser(_psiA, _IA, 0), _vser(_psiB, _IB, 1), _vser(_psiC, _IC, 2))
    else:
        _vA = _vB = _vC = _iAm = _iBm = _iCm = np.zeros(0)
    VA, VB, VC = _vA.tolist(), _vB.tolist(), _vC.tolist()
    Vpk = float(np.max(np.abs(VA + VB + VC))) if _psiA else 0.0
    # Terminal electrical input ⟨Σ v·i⟩ (EXACTLY 0 at no-load), time-weighted
    # over the window, each step's voltage times the SAME step's mean current —
    # the CN pairing, under which stored magnetic energy telescopes away over a
    # closed period, so what is left is the power the field converted or
    # dissipated.  IA/IB/IC are PER-BRANCH conductor currents, so the machine
    # total carries the n_parallel factor.
    if _IA:
        _pw_v = (_vA * _iAm + _vB * _iBm + _vC * _iCm)
        P_elec_in2 = float(np.sum(_pw_v * _dts_v) / np.sum(_dts_v)) \
            * float(n_parallel)
        _P_R2 = float(np.sum(R_phase * (_iAm ** 2 + _iBm ** 2 + _iCm ** 2)
                             * _dts_v) / np.sum(_dts_v)) * float(n_parallel)
    else:
        P_elec_in2 = 0.0
        _P_R2 = 0.0
    _ang = [(k / n_total) * period_mech * n_periods for k in range(n_total)]
    _V_ang = [a - 0.5 * period_mech * n_periods / max(n_total, 1) for a in _ang]
    # POWER BALANCE of the field, from the SAME terminal quantities: what the
    # winding put into the field (P_in minus the R·i² it burned itself) against
    # the air-gap power T·ω plus every loss the field solve itself dissipated
    # (coupled eddy: copper AC, magnet, shaft, sleeve).  The post-processed
    # iron loss is NOT in the field (laminated iron is σ = 0 in the solve), so
    # it is not in this balance.  Its residual is the discretisation's.
    _p_eddy_solved = ((float(P_cu_ac_avg2) if eddy else 0.0)
                      + ((float(np.mean(_ed_mag)) + float(np.mean(_ed_sh))
                          + (float(np.mean(_ed_sl)) if _ed_sl else 0.0))
                         if (eddy and rotor_eddy and _ed_mag) else 0.0))
    _p_em_in2 = float(P_elec_in2 - _P_R2)
    _p_gap_bal = float(Tavg * _omega_m2)
    _power_balance = {
        "P_in_W": float(P_elec_in2),
        "P_winding_R_W": float(_P_R2),
        "P_field_in_W": _p_em_in2,
        "P_airgap_W": _p_gap_bal,
        "P_field_solved_loss_W": float(_p_eddy_solved),
        "residual_W": float(_p_em_in2 - _p_gap_bal - _p_eddy_solved),
        "residual_rel": (float((_p_em_in2 - _p_gap_bal - _p_eddy_solved)
                               / abs(P_elec_in2)) if abs(P_elec_in2) > 0 else None),
        "note": ("P_field_in = P_in − R·⟨ī²⟩ (winding power into the field); "
                 "balance against T_mean·ω + the field's own solved eddy "
                 "losses. Post-processed iron loss is outside the field."),
    }
    log.info("P2 power balance | P_in %.4g W = R·i² %.4g + field %.4g; field "
             "vs T·ω %.4g + solved eddy %.4g -> residual %.4g W (%s)",
             P_elec_in2, _P_R2, _p_em_in2, _p_gap_bal, _p_eddy_solved,
             _power_balance["residual_W"],
             ("%.3g %%" % (100.0 * _power_balance["residual_rel"])
              if _power_balance["residual_rel"] is not None else "n/a"))

    # ═══════════════════════════════════════════════════════════════════════
    #  STAR / DELTA — the terminal connection
    # ═══════════════════════════════════════════════════════════════════════
    # The FEM never sees it: a winding carries the current it carries, and this
    # solver is driven by the WINDING (phase) current either way.  What the
    # connection changes is the mapping to the terminals, and one piece of real
    # physics that exists in delta only.
    #
    #   star   V_line = sqrt(3)·V_phase    I_line = I_phase
    #   delta  V_line = V_phase            I_line = sqrt(3)·I_phase
    #
    # so delta buys sqrt(3) more turns on the same bus, and that is the whole
    # reason to want it.
    #
    # THE DELTA-ONLY LOSS.  The phase flux linkage of a concentrated winding
    # carries a ZERO-SEQUENCE triplen harmonic — all three phases in phase.  In
    # star it appears phase-to-neutral and cancels line-to-line, so nothing
    # flows and nothing is dissipated.  Close the delta and those three EMFs
    # add round the loop, driving a circulating current against 3·Z0 — i.e.
    # E_h/Z0(h·f) per phase, at load and at no load alike.
    #
    # What limits it is L0, and on THIS winding that is the surprise worth
    # measuring rather than assuming: a concentrated non-overlapping winding
    # has almost no mutual coupling between phases, so L0 ~ L_self ~ Ld instead
    # of the small leakage-only L0 of a distributed winding.  The probe below
    # measures it on the RUN'S OWN iron state (nu_all2 at the operating point),
    # which is what makes its absolute value usable — the same probe on the
    # I = 0 linear state reads ~4.5x high because the magnets have already
    # pushed the teeth up the BH curve.
    #
    # R at the triplen: the solved AC extra is proximity-dominated, so it
    # scales as h^2.  That is an extrapolation, and it is the loosest step
    # here — it is labelled in the result rather than buried.
    _sd_mode = str(star_delta or "star").lower()
    _is_delta = _sd_mode.startswith("d")
    _L0_mH = None
    _P_circ = 0.0
    _circ_rows = []
    if (_is_delta or _os_sb.environ.get("SB_ZERO_SEQ_PROBE")) and _psiA:
        try:
            _P02 = f_coil2['A'] + f_coil2['B'] + f_coil2['C']
            # DIFFERENTIAL stiffness at the operating field, not the secant
            # one.  nu_all2 is nu(|B|) = H/B; a small-signal inductance rides
            # on dH/dB, and on iron this far up the curve the two differ by
            # ~2.7x — measured, by reading L0 = 0.125 mH off the secant against
            # a bench Ld of 0.045 mH on the same machine.  K + tangent2 IS the
            # differential operator: it is the Jacobian the eddy Newton uses,
            # so this probe and the solve agree on the iron by construction.
            _Km_d, _info_d = _p2.Kpw(A2)
            _Kz_diff = _Km_d
            if _info_d is not None:
                _Tg_d = _p2.tangent2(_info_d)
                if _Tg_d is not None:
                    _Kz_diff = _Km_d + _Tg_d
            _L0s = []
            for _kz in sorted({0, int(n_total) // 3, (2 * int(n_total)) // 3}):
                _Pro_z, _out_z = _proj.build(int(_kz))
                _free_z = np.setdiff1d(np.arange(_Pro_z.shape[1]), _out_z)
                _Kz = _Kz_diff
                _Kffz = (_Pro_z.T @ _Kz @ _Pro_z).tocsr()[_free_z][:, _free_z].tocsc()
                _Xz = _p2.solve_ff(
                    _Kffz, np.column_stack([
                        np.asarray(_Pro_z.T @ _P02).ravel()[_free_z],
                        np.asarray(_Pro_z.T @ f_coil2['A']).ravel()[_free_z]]),
                    spd=True)
                _L0s.append(_psi2(_p2.pad2(_Pro_z, _free_z, _Xz[:, 0]))[0])
                if _kz == 0:
                    # Phase A alone: psi_A = L_aa (self), psi_B/psi_C = L_ab
                    # (mutual).  L0 MUST equal L_aa + 2.L_ab — it is the same
                    # factorisation, so a mismatch means the coil map, not the
                    # machine.  The ratio L0/(L_aa - L_ab) is the one number
                    # that says whether a delta is safe on this winding:
                    # ~1 on a concentrated winding (no mutual), much less than
                    # 1 on a distributed one.
                    _La = _psi2(_p2.pad2(_Pro_z, _free_z, _Xz[:, 1]))
                    log.info("zero-sequence probe: L_aa = %.5g mH, L_ab = "
                             "%.3g / %.3g mH (%.2f %% of self) -> L0 = %.5g mH "
                             "against L_aa+2.L_ab = %.5g mH (%s); balanced-"
                             "drive L_aa-L_ab = %.5g mH, so L0/Ld = %.3f",
                             1e3 * _La[0], 1e3 * _La[1], 1e3 * _La[2],
                             100.0 * 0.5 * (_La[1] + _La[2]) / max(abs(_La[0]), 1e-30),
                             1e3 * _L0s[-1], 1e3 * sum(_La),
                             ("consistent" if abs(sum(_La) - _L0s[-1])
                              <= 0.02 * max(abs(_L0s[-1]), 1e-30)
                              else "INCONSISTENT"),
                             1e3 * (_La[0] - 0.5 * (_La[1] + _La[2])),
                             _L0s[-1] / max(_La[0] - 0.5 * (_La[1] + _La[2]),
                                            1e-30))
            _L0 = float(np.mean(_L0s))       # H per phase, rotor-angle averaged
            _L0_mH = 1e3 * _L0
            # zero-sequence harmonics of psi, straight off the solved waveforms
            _pz = (np.asarray(_psiA, float) + np.asarray(_psiB, float)
                   + np.asarray(_psiC, float)) / 3.0
            _nz = _pz.size
            _Fz = np.abs(np.fft.rfft(_pz) / _nz * 2.0)
            if _nz % 2 == 0 and _Fz.size > 1:
                _Fz[-1] *= 0.5          # Nyquist bin: one real cosine, |C|
            _w_e = 2.0 * math.pi * float(f_elec)
            _Iph2 = (float(_I_ph_rms_solved or I_phase_rms) ** 2) or 1.0
            _Rac1 = (float(P_cu_ac_avg2) / (3.0 * _Iph2)) if eddy else 0.0
            # EVERY bin of the zero-sequence flux the window resolves — no
            # harmonic list (it was 3, 9, 15 only; owner 2026-09-24: no
            # truncation).  Balanced windings put the zero sequence at the
            # triplens; whatever sits elsewhere is zero-sequence EMF all the
            # same and circulates the same way.  The window spans n_periods
            # electrical periods, so bin m is harmonic m / n_periods.
            _npz = max(1.0, float(n_periods))
            for _m in range(1, _Fz.size):
                _h = _m / _npz
                _Eh = _h * _w_e * float(_Fz[_m]) / math.sqrt(2.0)   # V rms
                if _Eh <= 0.0:
                    continue
                _Rh = float(R_phase) + _Rac1 * (_h ** 2)
                _Zh = math.hypot(_Rh, _h * _w_e * _L0)
                _Ih = _Eh / max(_Zh, 1e-12)
                _Ph = 3.0 * _Ih * _Ih * _Rh
                _P_circ += _Ph
                _circ_rows.append({"harmonic": round(_h, 4),
                                   "E_rms_V": round(_Eh, 2),
                                   "R_ohm": round(_Rh, 6),
                                   "X_ohm": round(_h * _w_e * _L0, 5),
                                   "I_circ_rms_A": round(_Ih, 2),
                                   "P_W": round(_Ph, 1)})
            log.info("%s connection: L0 = %.5g mH (rotor-angle mean, run's own "
                     "iron), circulating %s -> %.0f W added to the copper",
                     _sd_mode.upper(), _L0_mH,
                     ", ".join("h%g %.1f A" % (r["harmonic"], r["I_circ_rms_A"])
                               for r in _circ_rows
                               if r["I_circ_rms_A"] >= 0.05) or "none",
                     _P_circ)
        except Exception as _e_sd:
            log.warning("star/delta zero-sequence step failed (%s) — the "
                        "connection's terminal mapping is still reported, the "
                        "circulating loss is NOT", _e_sd)
            _P_circ = 0.0
    if not _is_delta:
        _P_circ = 0.0                # star cannot circulate: no closed loop
    # TERMINAL line-to-line voltage, per connection, off the solved waveforms
    # rather than sqrt(3)x a phase peak.  Neither connection puts the winding's
    # zero-sequence EMF on the terminals: star cancels it line-to-line, delta
    # drops it across Z0 driving the circulating current above.  So both are
    # the NON-TRIPLEN part — but star sees sqrt(3) of it and delta sees one.
    # This is the number the DC bus has to cover, and reading it off V_peak
    # (which still carries the triplen) overstates it by ~10 % on this winding.
    _V_ll_pk = 0.0; _V_ll_rms = 0.0
    if _psiA:
        _va = np.asarray(VA, float); _vb = np.asarray(VB, float)
        _vc = np.asarray(VC, float)
        if _is_delta:
            _v0 = (_va + _vb + _vc) / 3.0
            _lls = (_va - _v0, _vb - _v0, _vc - _v0)
        else:
            _lls = (_va - _vb, _vb - _vc, _vc - _va)
        _V_ll_pk = float(max(np.max(np.abs(p)) for p in _lls))
        _V_ll_rms = float(np.mean([np.sqrt(np.mean(p ** 2)) for p in _lls]))
    # ── DC-LINK CURRENT (PWM only) ───────────────────────────────────────
    # The bus side of the SAME bridge, from the SAME comparator the circuit
    # integrated: Σ_phase s_phase(t)·i_phase(t).  Not an inverter model bolted
    # on afterwards — with pole voltages ±v_bus/2 and a floating neutral,
    # V_bus·i_dc equals Σ v_pole·i identically, so this is the terminal power
    # the run already solved, read on the other side of the switches.  It is
    # what makes "the machine charges its battery" a measurement rather than an
    # arithmetic wish.
    _dc_link = None
    _dcls = getattr(_src, "dc_link_series", None)
    if callable(_dcls) and _IA:
        try:
            _dc_link = _dcls(rotor_angle_deg=_ang, i_a=_IA, i_b=_IB,
                             i_c=_IC, n_parallel=int(n_parallel)) or None
            if _dc_link:
                log.info("P2 PWM DC link: I_dc mean %.3f A (rms %.3f, pp %.3f)"
                         " on a %.1f V bus -> %.1f W",
                         _dc_link["I_dc_mean_A"], _dc_link["I_dc_rms_A"],
                         _dc_link["I_dc_ripple_pp_A"], float(_src.v_bus),
                         float(_src.v_bus) * _dc_link["I_dc_mean_A"])
        except Exception as _dce:   # noqa: BLE001 — never sink a solved run
            log.warning("DC-link postprocess failed: %s", _dce)
            _dc_link = None
    # ── WHAT THE SOURCE SAYS ABOUT ITSELF ────────────────────────────────
    # The report blocks (`excitation`, `pwm`, `custom_current`, `bldc`) come
    # from the SOURCE, not from a chain of `if drive == ...` in the payload:
    # one place to add a source, one place its numbers are described.  The
    # run-dependent half it cannot know — f_elec, the reported window's period
    # and step counts, the settle composition, the solved DC-link series — is
    # handed in.  n_periods / n_total are already the TRIMMED (reported)
    # values here, which is what the oscilloscope views must be drawn over.
    _desc_ctx = {
        "f_elec": float(f_elec), "n_periods": float(n_periods),
        "n_steps_per_period": int(n_steps_per_period), "n_total": int(n_total),
        "sched_mixed": bool(_sched_mixed), "progress_comp": _progress_comp,
        "c_nspp": int(_c_nspp), "fine_frames": int(_fine_frames),
        "fine_settle_periods": int(_fine_frames // max(int(_v_nspp), 1)),
        "dc_orbit_corrections": int(0 if _dc_orbit is None
                                    else _dc_orbit.corrections),
        "v_nspp": int(_v_nspp), "dc_link": _dc_link,
        "settle_periods": int(_v_settle_periods), "n_parallel": int(n_parallel),
    }
    try:
        _desc = _src.describe(_desc_ctx) or {}
    except TypeError:       # a source whose describe() takes no context
        _desc = _src.describe() or {}
    log.info("P2 belt transient done: %d frames reported (%d SOLVED incl. "
             "settling) in %.1f s, T_avg=%.5f Nm, ripple_pp=%.6g Nm, "
             "ripple_pct=%s, max "
             "nonlinear resid=%.2e, picard-fallback frames=%s",
             n_total, _n_solved, _t.time() - t0, Tavg, T_ripple_pp,
             ("undefined (near-zero mean)" if Trip_raw is None
              else "%.2f%%" % Trip_raw),
             _pic_res_max, _pic_fallback or "none")
    # What the two per-run caches actually saved, so a slow run can be read
    # instead of guessed: a healthy run reuses ONE symbolic factorization for a
    # whole frame's Newton sweep and serves ~45 % of its Kpw calls from the memo.
    log.info("P2 cost: %d linear solves on %d symbolic factorizations "
             "(%.1f solves/analysis), Kpw %d assembled + %d memo hits, "
             "perturbed-pivot solves=%d; Cholesky %d solves on %d analyses "
             "(%d declined to LU, %d failures)",
             _p2.pardiso_solves, _p2.pardiso_analyses,
             _p2.pardiso_solves / max(_p2.pardiso_analyses, 1),
             _p2.kpw_calls, _p2.kpw_hits, _p2.pardiso_perturbed,
             _p2.spd_solves, _p2.spd_analyses, _p2.spd_declined,
             _p2.spd_failures)
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
    # ── Demag aggregate for the summary card ─────────────────────────────────
    # ONE number an engineer can act on: the AREA-weighted mean Br the magnets
    # kept.  To first order (T ∝ ψ_pm ∝ ∫Br dA) its deficit bounds the torque /
    # EMF loss, which is what "demagnetization coefficient" should mean — the
    # worst single element is a corner statistic, alarming and unrepresentative
    # on its own, so it ships as context, not as the headline.
    # magnet_scale is divided OUT: it is the torque-decomposition knob, not
    # demagnetisation, and folding it in would report a deliberate 0.5× PM run
    # as "50 % demagnetised".
    _demag_sum = None
    if demag and _dmst is not None and _mag_idx.size:
        _ar_mag = _triangle_areas(half["r"]["mesh"])[_mag_idx]
        _brm = _br_glob[_mag_idx] / max(float(magnet_scale), 1e-12)
        _wsum = max(np.sum(_ar_mag), 1e-30)
        _kept = float(np.sum(_brm * _ar_mag) / _wsum)
        # ENERGY criterion (user's spec 2026-08-23): the magnet's energy product
        # (BH)max = Br²/(4·μ0·μrec) — partial demag drops the recoil line
        # parallel (same μrec), so per element (BH)' = k²·(BH) exactly, and the
        # volume-weighted ⟨k²⟩ is the fraction of magnet ENERGY kept.  It also
        # speaks grade language: the number in N52/F52 IS (BH)max in MGOe, so
        # kept-energy × nominal grade = the grade this magnet now effectively is.
        _kept_bh = float(np.sum(_brm * _brm * _ar_mag) / _wsum)
        # INVARIANT (user's observation 2026-08-23): after the demag pre-pass
        # swept a whole electrical period, every magnet has seen the same
        # worst-case MMF — the per-magnet integrals must agree.  One magnet
        # would suffice arithmetically; computing all costs microseconds and
        # turns the assumption into a CHECK: a spread here means the sweep did
        # not cover the period or the model lost its symmetry, and that must
        # surface, not hide inside an average.
        _perm = []
        _ar_r_all = _triangle_areas(half["r"]["mesh"])
        for _dmg in getattr(_dmst, "mags", []):
            _ix = np.asarray(_dmg["idx"], int)
            _kk = _br_glob[_ix] / max(float(magnet_scale), 1e-12)
            _aa = _ar_r_all[_ix]
            _perm.append(float(np.sum(_kk * _kk * _aa) / max(np.sum(_aa), 1e-30)))
        _spread = (100.0 * (max(_perm) - min(_perm))) if _perm else 0.0
        if _spread > 0.5:
            log.warning("demag per-magnet spread %.2f %% (kept-energy min %.2f, "
                        "max %.2f %%) — magnets should be IDENTICAL after a "
                        "full-period sweep; the state is asymmetric",
                        _spread, 100.0 * min(_perm), 100.0 * max(_perm))
        _demag_sum = {
            "per_magnet_spread_pct": round(_spread, 3),
            "bh_kept_vol_pct": round(100.0 * _kept_bh, 3),
            "bh_loss_pct": round(100.0 * (1.0 - _kept_bh), 3),
            "br_kept_vol_pct": round(100.0 * _kept, 3),
            "loss_pct": round(100.0 * (1.0 - _kept), 3),
            "area_derated_pct": round(100.0 * float(
                np.sum(_ar_mag[_brm < 0.999]) / _wsum), 2),
            "br_corner": _demag_corner_diag(
                half["r"]["mesh"], _mag_idx, _brm, _ar_mag,
                getattr(_dmst, "mags", [])),
        }
    # ── dq QUANTITIES of this operating point ────────────────────────────────
    # Mean flux linkages and currents in the rotor (d-q) frame, from the same
    # series the circuit already carries.  The transform angle is the d-axis
    # frame: the calibration defines DAXIS so that γ=0 is the Q-axis, i.e. the
    # d-axis itself sits 90° el behind the γ=0 current — hence the −90 here.
    # PHASE quantities at the terminals: the I_* series are per-branch, and the
    # phase current is branch × n_parallel (parallel branches share the flux).
    #
    # The FRAME IS PROVEN, NOT TRUSTED: the dq torque identity
    #   T = 1.5·p·(ψd·iq − ψq·id)
    # is evaluated against the energy-method mean torque and shipped as
    # dq_torque_check_pct.  A sign or convention slip here does not produce a
    # subtly wrong inductance — it produces a check that reads 200 %.
    _dq = {}
    try:
        import math as _mdq
        _thser = [_mdq.radians(a * pole_pairs + daxis_eff - 90.0) for a in _ang]
        from motor_ai_sim.simulation.drive import park as _park
        _np_ph = float(max(1, int(n_parallel)))
        _ds, _qs, _ids, _iqs = [], [], [], []
        for _k in range(len(_ang)):
            _pd, _pq = _park(_psiA[_k], _psiB[_k], _psiC[_k], _thser[_k])
            _idv, _iqv = _park(_IA[_k] * _np_ph, _IB[_k] * _np_ph,
                               _IC[_k] * _np_ph, _thser[_k])
            _ds.append(_pd); _qs.append(_pq); _ids.append(_idv); _iqs.append(_iqv)
        _psid = float(np.mean(_ds)); _psiq = float(np.mean(_qs))
        _idm = float(np.mean(_ids)); _iqm = float(np.mean(_iqs))
        _Tdq = 1.5 * float(pole_pairs) * (_psid * _iqm - _psiq * _idm)
        _chk = (100.0 * abs(_Tdq - float(Tavg)) / abs(float(Tavg))
                if abs(float(Tavg)) > 1e-9 else None)
        # FULL precision (review 2026-09-30): these are DATA a passport
        # interpolates at a 0.5 % flux target — ψ rounded to 1 µWb is 0.1 %
        # of the Ø40's 1 mWb, and i_d rounded to 0.01 A near zero current is
        # no bound at all.  Rounding belongs to the display.
        _dq = {
            "psi_d_Wb": _psid, "psi_q_Wb": _psiq,
            "i_d_A": _idm, "i_q_A": _iqm,
            "T_dq_Nm": _Tdq,
            "dq_torque_check_pct": _chk,
        }
    except Exception as _edq:   # noqa: BLE001
        _dq = {"dq_error": f"{type(_edq).__name__}: {_edq}"}

    # ── INCREMENTAL (frozen-permeability) d-q INDUCTANCES of this point ──────
    # The per-position rows averaged over the reported window, with the
    # modulation they were averaged over reported beside them: L(θ) carries the
    # slot harmonics, so a spread of tens of per cent means "this machine's
    # inductance depends on where the rotor is", which is a finding and not an
    # error bar to hide.  `superposition_pct` is the method's own self-check —
    # with ν frozen the field must satisfy ψd = ψ*_PM + Ld·i_d + Ldq·i_q
    # EXACTLY, so anything but ~0 means the frozen operator is not the one that
    # produced the frame (a coupled-eddy or demag-rebuilt frame will say so).
    _inc = {}
    if _inc_rows:
        try:
            def _mn(_k):
                return float(np.mean([_r[_k] for _r in _inc_rows]))

            def _spread(_k):
                _v = [abs(_r[_k]) for _r in _inc_rows]
                return float(100.0 * (max(_v) - min(_v)) / max(np.mean(_v), 1e-30))
            # BOTH AXES.  ψq is a small difference of large numbers, so each
            # residual is normalised by |ψ_dq| rather than by its own
            # component — a 1 mWb miss is a 1 mWb miss whichever axis it lands
            # on, and dividing it by a near-zero ψq would report a per cent
            # that means nothing.
            _sup = []
            for _r in _inc_rows:
                _ref_p = max(math.hypot(_r["psi_d_Wb"], _r["psi_q_Wb"]), 1e-30)
                _pd_e = (_r["psi_d_pm_frozen_Wb"] + _r["Ld_H"] * _r["i_d_A"]
                         + _r["Ldq_H"] * _r["i_q_A"] - _r["psi_d_Wb"])
                _pq_e = (_r["psi_q_pm_frozen_Wb"] + _r["Ldq_H"] * _r["i_d_A"]
                         + _r["Lq_H"] * _r["i_q_A"] - _r["psi_q_Wb"])
                _sup.append(100.0 * max(abs(_pd_e), abs(_pq_e)) / _ref_p)
            _Ldm, _Lqm = _mn("Ld_H"), _mn("Lq_H")
            _inc = {
                "Ld_mH": round(1e3 * _Ldm, 6),
                "Lq_mH": round(1e3 * _Lqm, 6),
                "Ldq_mH": round(1e3 * _mn("Ldq_H"), 6),
                "saliency_Lq_over_Ld": (round(_Lqm / _Ldm, 4)
                                        if abs(_Ldm) > 1e-15 else None),
                # The magnets' own flux linkage IN THE LOADED IRON.  Compared
                # with the no-load psi_PM this IS the cross-saturation sag —
                # the term the chord Ld divides by i_d and calls an inductance.
                "psi_d_pm_frozen_Wb": round(_mn("psi_d_pm_frozen_Wb"), 8),
                "psi_q_pm_frozen_Wb": round(_mn("psi_q_pm_frozen_Wb"), 8),
                "i_d_A": round(_mn("i_d_A"), 3),
                "i_q_A": round(_mn("i_q_A"), 3),
                "samples": len(_inc_rows),
                "rotor_positions": [int(_r["frame"]) for _r in _inc_rows],
                "spread_pct": {"Ld": round(_spread("Ld_H"), 2),
                               "Lq": round(_spread("Lq_H"), 2)},
                "reciprocity_pct": round(
                    max(_r["reciprocity_pct"] for _r in _inc_rows), 3),
                "superposition_pct": round(max(_sup), 3),
                "method": ("frozen-permeability incremental: the per-element ν "
                           "of the converged loaded field is held fixed and a "
                           "unit d- and q-axis current solved on it "
                           "(dψ/di, exact for a linear operator). "
                           "Magnetostatic — on a coupled-eddy run the field's "
                           "own ψ/i also carries the AC redistribution, which "
                           "is impedance and not inductance."),
            }
        except Exception as _ei2:   # noqa: BLE001
            _inc = {"inc_ldq_error": f"{type(_ei2).__name__}: {_ei2}"}

    # ── SIX-PHASE: per-set flux / voltage and the VSD inductances ───────────
    _six_out = None
    if f_set2 is not None:
        _six_out = dict(_six_blk)
        try:
            if len(_six_psi) >= 3:
                _th6 = np.array([r[0] for r in _six_psi])
                _M6 = np.column_stack([np.cos(_th6), np.sin(_th6),
                                       np.ones_like(_th6)])
                _w_el = 2.0 * math.pi * float(f_elec)
                _ps = []
                for _s in (1, 2):
                    _y = np.array([r[_s] for r in _six_psi])
                    _cf = np.linalg.lstsq(_M6, _y, rcond=None)[0]
                    _amp = float(math.hypot(_cf[0], _cf[1]))
                    _ps.append({"set": _s, "psi1_A_Wb": round(_amp, 6),
                                "psi1_angle_deg": round(math.degrees(
                                    math.atan2(_cf[1], _cf[0])), 2),
                                "V1_flux_peak_V": round(_w_el * _amp, 2)})
                _six_out["per_set_flux"] = _ps
                _six_out["per_set_flux_note"] = (
                    "fundamental of each set's phase-A flux linkage (one "
                    "branch); V1_flux = omega * psi1 — the loaded EMF, "
                    "without the R drop")
            if _six_rows:
                def _m6(_k):
                    return float(np.mean([_r[_k] for _r in _six_rows]))

                def _sp6(_k):
                    _v = [abs(_r[_k]) for _r in _six_rows]
                    return float(100.0 * (max(_v) - min(_v))
                                 / max(np.mean(_v), 1e-30))
                _ldset = _m6("Ld_set_H")
                _six_out["inductances"] = {
                    "Ld_set_mH": round(1e3 * _ldset, 6),
                    "Lq_set_mH": round(1e3 * _m6("Lq_set_H"), 6),
                    "Lxy_mH": round(1e3 * _m6("Lxy_H"), 6),
                    "Lxy_min_mH": round(1e3 * min(_r["Lxy_min_H"] for _r in _six_rows), 6),
                    "Lxy_max_mH": round(1e3 * max(_r["Lxy_max_H"] for _r in _six_rows), 6),
                    "Lxy_pct_of_Ld": (round(100.0 * _m6("Lxy_H") / _ldset, 3)
                                      if abs(_ldset) > 1e-15 else None),
                    "spread_pct": {"Ld_set": round(_sp6("Ld_set_H"), 2),
                                   "Lxy": round(_sp6("Lxy_H"), 2)},
                    "dq_xy_coupling_pct": round(max(
                        _r["dq_xy_coupling_pct"] for _r in _six_rows), 4),
                    "reciprocity_pct": round(max(
                        _r["reciprocity_pct"] for _r in _six_rows), 4),
                    "samples": len(_six_rows),
                    "reference": "per SET phase (a set = half the paths); "
                                 "L_xy % is of the per-set L_d, the ripple "
                                 "model's bridge-phase reference",
                    "method": ("frozen permeability at the operating point: "
                               "the 6x6 incremental L of the two sets "
                               "(six unit-current back-solves on the loaded "
                               "frame's frozen ν) projected on the orthonormal "
                               "VSD — d/q = the air-gap plane, x-y = its "
                               "complement without the zero sequences"),
                }
            elif not _six_blk.get("xy_probe_valid"):
                _six_out["inductances_note"] = (
                    "L_xy not measured on this model: the sector does not "
                    "repeat the set pattern — the full-ring probe "
                    "(measure_six_phase_inductances) measures it")
        except Exception as _e6:   # noqa: BLE001 — a result block, never fatal
            _six_out["error"] = f"{type(_e6).__name__}: {_e6}"

    _vw_all_eligible = bool(_T_vw) and len(_T_vw) == len(_T2) and all(
        value is not None for value in _T_vw)
    _vw_mean = (float(np.mean(_T_vw)) if _vw_all_eligible else None)
    _vw_reason_counts = {}
    for _vw_reason in _T_vw_reason:
        if _vw_reason is not None:
            _vw_reason_counts[_vw_reason] = _vw_reason_counts.get(_vw_reason, 0) + 1
    _vw_diagnostics = {
        "status": ("uncertified_diagnostic" if _vw_enabled
                   else "disabled (opt-in: SB_P2_VIRTUAL_WORK=1)"),
        "enabled": bool(_vw_enabled),
        "frame_reasons": list(_T_vw_reason),
        "eligible_frame_count": sum(value is not None for value in _T_vw),
        "reported_frame_count": len(_T2),
        "ineligible_reason_counts": _vw_reason_counts,
        "mean_available": _vw_all_eligible,
        "mean_unavailable_reason": (None if _vw_all_eligible else
                                    "one_or_more_reported_frames_ineligible"),
        "angle_unit": "mechanical_radian",
        "slip_node_spacing_mechanical_rad": math.radians(float(spacing)),
        "sign_convention": "T = -d(magnetic_potential)/d(mechanical_angle)",
        "sector_multiplier": int(NS),
        "stack_length_m": float(p.stack_length),
        "scale_convention": "2D sector residual times stack length and sector count",
        "constraint_derivative": "L2 trace mortar at integer slip shift",
    }
    return {
        "method": "sliding_band_p2", "element_order": 2,
        **({"inc_ldq": _inc} if _inc else {}),
        **({"six_phase": _six_out} if _six_out is not None else {}),
        # WHICH ELECTRICAL FRAME THIS RUN WAS SOLVED IN.  gamma is measured
        # from the q-axis, and the q-axis is wherever the d-axis calibration
        # put it — so a run whose calibration landed on the wrong sample of
        # psi_A solves a DIFFERENT operating point than the gamma on screen,
        # with nothing in the result to say so.  It cost an hour of bisection
        # to recover this number for one run by fitting T and V_peak against a
        # gamma sweep; it is one float, and it belongs in every payload.
        "daxis_deg": round(float(daxis_eff), 4),
        "daxis_source": _daxis_src,
        # Optimizer candidates only (fix B): baseline reuse or own calibration,
        # and why.  Absent on every other solve.
        **({"daxis_policy": dict(_daxis_policy)} if _daxis_policy else {}),
        "gamma_effective_deg": round(float(gamma_deg), 4),
        "loss_model": _lm2,
        "demag_coef_per_tri": (_dcoef2.tolist() if _dcoef2 is not None else None),
        "demag_report": _drep2,
        "demag_summary": _demag_sum,
        "demag_field": _dfield2,
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        # What the run COST.  n_steps is the REPORTED window; these two are the
        # frames actually solved (settling included) and the wall seconds they
        # took, which is what the Simulation tab turns into its pre-run estimate.
        # Solver time only — the route's summary build and JSON serialisation
        # sit outside it, so this UNDERSTATES the click-to-chart wait slightly
        # and can never overstate it.
        "n_frames_solved": int(_n_solved),
        "voltage_settle_periods": int(_v_settle_periods),
        "voltage_settle_source_policy_periods": int(_settle.periods_static),
        "voltage_settle_selection_reason": _settle_selection_reason,
        # The converged settle (plain sinusoidal voltage): periods used, the
        # criterion, the stopping residual, each period's three quantities,
        # and what the reported period moved against the last settling one.
        # None on every fixed-count run.
        "voltage_settle": _voltage_settle,
        "solve_wall_s": round(float(_t.time() - t0), 1),
        # What the caller ASKED for, beside what actually ran.  The whole-node
        # snap silently changed the time resolution of every run whose requested
        # count was not a divisor of the slip-node grid; a consumer can now say
        # "requested 40 -> ran 36" instead of presenting 36 as if it were asked
        # for.  ``slip_nodes_per_period`` is the grid that decides the snap.
        "n_steps_per_period_requested": int(_req_steps),
        "steps_snapped": bool(int(n_steps_per_period) != int(_req_steps)),
        "cogging_sampling_purpose": _cogging_sampling["purpose"],
        "cogging_target_raw_samples_per_cycle": _cogging_sampling[
            "target_raw_samples_per_cycle"],
        "cogging_cycles_per_electrical_period": _cogging_sampling[
            "cycles_per_electrical_period"],
        "cogging_min_required_steps_per_period": _cogging_sampling[
            "min_required_steps_per_period"],
        "cogging_final_quality_min_required_steps_per_period": _cogging_sampling[
            "final_quality_min_required_steps_per_period"],
        "cogging_raw_samples_per_cycle": _cogging_sampling[
            "raw_samples_per_cycle"],
        "cogging_sampling_sufficient": _cogging_sampling["sufficient"],
        "cogging_sampling_final_quality_sufficient": _cogging_sampling[
            "final_quality_sufficient"],
        "cogging_sampling_auto_raised": _cogging_sampling["auto_raised"],
        "cogging_sampling_reason": _cogging_sampling["reason"],
        # Build provenance (see the _mesh_build_trace() read after the build):
        # empty events = the requested build ran; anything listed = a silent
        # fallback fired and this result's ripple floor is NOT comparable with
        # a cleanly built one.  structured_gap_effective says whether the belt
        # / structured cells actually materialised (False on the free-gap
        # fallback even when structured_gap=True was requested).
        "mesh_build_events": list(_build_prov["events"]),
        # Deterministic build-path decisions (a sleeve → geometry mesher):
        # reported, never a rejection.
        "mesh_build_notes": list(_build_prov.get("notes") or []),
        "structured_gap_effective": bool(_build_prov["structured_gap_effective"]),
        "slip_nodes_per_period": int(_nodes_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec,
        "dt_s": dt, "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": _tt, "rotor_angle_deg": _ang,
        "P2_transient_sample_history": _p2_transient_history,
        "T_em_Nm": _T2, "T_avg_Nm": Tavg, "T_ripple_pct": Trip_raw,
        "gap_layers_requested": _gap_layers_req,
        "gap_layers_effective": float(gap_layers),
        "gap_layers_note": _gap_layers_note,
        "T_coulomb_series": _coul2["T_coulomb_series"],
        "T_avg_coulomb_Nm": _coul2["T_avg_coulomb_Nm"],
        "T_ripple_pp_coulomb": _coul2["T_ripple_pp_coulomb"],
        "coulomb_torque": {_k: _v for _k, _v in _coul2.items()
                           if _k != "T_coulomb_series"},
        "T_em_virtual_work_diagnostic_Nm": list(_T_vw),
        "T_avg_virtual_work_diagnostic_Nm": _vw_mean,
        "virtual_work_diagnostics": _vw_diagnostics,
        "T_ripple_raw_pct": Trip_raw, "T_ripple_filt_pct": Trip_raw,
        "T_ripple_pp_Nm": T_ripple_pp, "T_ripple_raw_pp_Nm": T_ripple_pp,
        "T_ripple_pct_available": Trip_raw is not None,
        "T_ripple_pct_reason": (None if Trip_raw is not None
                                else "undefined: mean torque is zero or near zero"),
        # Deprecated aliases retain raw values; no filtering/noise estimate.
        "T_noise_floor_pct": None, "torque_filter_applied": False,
        "T_em_raw_Nm": list(_T2), "T_em_filt_Nm": _T_report,
        "torque_method": _torque_method,
        # Which MEAN the reported T_avg carries: "terminal_work" (eligible
        # conservative current-drive runs), "flux_linkage_space_vector" (every
        # other loaded run — eddy / demag / voltage / PWM) or "raw_maxwell"
        # (≤ 1 A per branch on an ineligible run). The ripple is raw Maxwell AC
        # in all three.
        "T_mean_method": _torque_mean_source,
        "torque_method_diagnostics": _torque_method_diag,
        "T_avg_maxwell_Nm": T_maxwell_avg,
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp,
        "T_em_maxwell_Nm": list(_T2raw),
        "psi_A_Wb": _psiA, "psi_B_Wb": _psiB, "psi_C_Wb": _psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        # Each V sample is its STEP's voltage (the Crank–Nicolson row, see
        # _step_voltage_series), located at the step midpoint — these angles.
        "V_rotor_angle_deg": _V_ang,
        "voltage_derivative": "crank_nicolson_step_mean",
        "power_balance": _power_balance,
        "I_A": _IA, "I_B": _IB, "I_C": _IC,
        # rms terminal phase current, SOLVED — present only where the current
        # was an answer rather than the input (see the copper recompute above).
        "I_phase_rms_solved_A": _I_ph_rms_solved,
        # Mean |B| over the air-gap clearance, one value per rotor position.
        # Averaged over the period into `B_gap_mean_T` by the summary builder,
        # the same path `P_core_W` takes.
        "B_gap_mean_T": [round(float(v), 6) for v in _bgap2],
        "P_cu_W": P_cu_ser2, "P_fe_W": P_fe_ser2,
        "P_mag_eddy_W": P_mag_ser2, "P_shaft_eddy_W": P_shaft_ser2,
        # Retaining sleeve — all zeros (and absent from the loss map) on the
        # machines that have none, which is all of them but the Ø200.
        "P_sleeve_eddy_W": P_sleeve_ser2,
        "P_loss_total_W": P_tot_ser2,
        "P_cu_dc_W": P_cu_dc2, "P_cu_ac_W": P_cu_ac_ser2,
        # Coupled σ·∂A/∂t solve (eddy=True) — the REPORTED copper AC / magnet
        # / shaft above come from these when it ran; the modelled numbers are
        # kept beside them as the cross-check (see the swap block).
        "eddy_coupled": bool(eddy),
        # Did the CONDUCTING ROTOR (magnet + shaft σ·∂A/∂t) actually get solved?
        # `rotor_eddy` is an ASK; this is the answer, and under an imposed
        # voltage the two used to differ silently (the flag was force-dropped
        # inside the solver, so P_solid came back as a zero meaning "not
        # solved").  The route keys its "not solved" card flag off THIS, so the
        # UI can never again attribute a solved zero to a dropped model — or an
        # unsolved zero to a lossless magnet.
        "rotor_eddy_solved": bool(rotor_eddy),
        # ── coupled-eddy warm-up honesty ─────────────────────────────────
        # How many discarded frames at θ<0 it took to settle the σ·∂A/∂t
        # history, and how much start-up transient was STILL left when the
        # reported window started (as a fraction of the settled solid loss).
        # A run whose residual is above eddy_warmup_tol has its P_mag/P_shaft
        # cycle means — and its efficiency — over-read by roughly that much,
        # and says so here instead of only in a log line.
        # DISCARDED frames at θ<0, all of them: the eddy warm-up AND (on an
        # eddy+demag run since 2026-09-05) the demag pre-pass that follows the
        # handoff.  One number, because what the tile's tooltip promises is that
        # nothing before the reported window was averaged into it — and both
        # stages are that.  `demag_prepass_frames` says how many of them were
        # the pre-pass, i.e. how many ran with the Br ratchet active.
        "eddy_warmup_frames": int(_n_warm) + int(_n_dmpre),
        "demag_prepass_frames": int(_n_dmpre),
        # how the eddy steady state was reached (coupled-eddy runs): "tdm"
        # (the periodic orbit solved directly; `tdm` is its record) or
        # "march"; `eddy_method_note` says in one line why a TDM request
        # was marched (not applicable, or failed)
        **({} if not eddy else {
            "eddy_method": _eddy_method,
            "eddy_method_requested": _eddy_method_requested,
            "eddy_method_note": ("; ".join(_n for _n in (
                _tdm_note, (_tdm_demag_note if (demag and _eddy_method == "tdm")
                            else None)) if _n) or None),
            "tdm": _tdm_info}),
        # EXPERIMENTAL / QUALIFIED (second Codex review, finding 11): a result
        # computed with the experimental demag shortcut is never a qualified
        # result — every consumer (summary, optimizer, reports) can see it.
        "tdm_experimental": bool(_tdm_info is not None and _eddy_method == "tdm"
                                 and ((_tdm_info.get("demag") or {})
                                      .get("experimental"))),
        "qualified": not bool(_tdm_info is not None and _eddy_method == "tdm"
                              and ((_tdm_info.get("demag") or {})
                                   .get("experimental"))),
        # ── WHOSE state this run continued (user 2026-09-06) ──────────────
        # A seeded eval is cheaper because it did not re-solve work another
        # point already paid for — and a result that does not SAY so cannot be
        # told apart from one that solved everything itself.  `warm_seeded` is
        # the eddy history, `demag_seeded` the irreversible Br map, and
        # `demag_seed_from` names the parent operating point and geometry.
        # Both are False on every interactive Simulation run: SB_SEED_FROM_
        # PREVIOUS is set by the sweep/optimizer coordinator only, and without
        # it no Br map is ever consumed.
        "warm_seeded": bool(_warm_seeded),
        "demag_seeded": bool(_dm_seeded),
        "demag_seed_from": (dict(_dm_seed_from) if _dm_seed_from else None),
        # None (not inf/NaN — this dict is serialised to JSON) when the march
        # was too short to say anything, which only happens if SB_EDDY_WARM
        # pinned it below the probe length.
        "eddy_warmup_resid": (None if (_warm_resid is None
                                       or not math.isfinite(_warm_resid))
                              else float("%.3g" % _warm_resid)),
        "eddy_warmup_tol": float(_EDDY_SETTLE_TOL),
        # ── THE VERDICT (user's 90-point sweep, 2026-09-07) ───────────────
        # The frame count above is what the warm-up COST; these three are
        # whether it WORKED, and they are what a consumer must key off before
        # it presents P_mag / P_shaft / η as physics.  `eddy_settled` False
        # means the σ·∂A/∂t start-up transient was still running when the
        # reported window opened, so those watts are a START-UP VALUE — the
        # 504 → 3558 → 6652 W shaft-loss scatter across neighbouring air gaps
        # in that sweep was exactly this, and it was read as demagnetisation.
        # `eddy_capped` says WHY it is not settled: the march ended at its
        # maximum allowed length (the one-shot extension to one electrical
        # period, an SB_EDDY_WARM pin, or the voltage settling schedule)
        # rather than by passing the test.  Today the two are complementary
        # by construction — there is exactly one extension — and they are
        # reported separately so a future multi-extension policy can say
        # "not settled AND not out of budget" without a consumer change.
        # A run with no coupled-eddy march (no eddy at all, or magnetostatic
        # frames) is settled by construction: nothing transient exists to be
        # averaged in, so it reports True / False and not a null.
        # AFFIRMATIVE ONLY (third Codex review, 2026-10-04): True when the
        # settle was MEASURED and passed (or there is no eddy march at all),
        # False when it failed, None when it is UNKNOWN (an unmeasured voltage
        # settle prefix, a PWM gauge that cannot judge) — never True by default.
        "eddy_settled": _eddy_verdict,
        "eddy_capped": bool(_warm_quiet is False),
        # ── DEMAG SETTLE (shared rule, 2026-09-30): did Br stop moving before
        # and during the reported period?  None = no demag (or no ratchet
        # ran); False = the reported window is a demag transient.
        # `steady_state` is the one verdict: eddy settled AND demag settled,
        # both affirmatively; unknown is not steady.
        "demag_settle": _dm_settle,
        "demag_settled": (None if _dm_settle is None else bool(_dm_settle["settled"])),
        # a single magnet element still moving > 1 % of Br0 per period: a
        # WARNING beside the verdict, never the verdict (owner 2026-10-04)
        "demag_warning": (None if _dm_settle is None else _dm_settle.get("warning")),
        "steady_state": bool(_eddy_verdict is True
                             and not (_dm_settle is not None
                                      and _dm_settle["settled"] is not True)),
        # …and WHY not, with the numbers (None when steady)
        "steady_state_note": (
            (_dm_settle or {}).get("note") if (_dm_settle is not None
                                              and _dm_settle["settled"] is not True)
            else _eddy_verdict_note),
        # PROVENANCE (owner 2026-09-27): settled, but the discarded warm-up
        # prefix was moved by periodic-accelerator jumps (the gauge then judged
        # >= MIN_VERIFY_PERIODS continuous periods after the last jump).
        "eddy_settled_via_accelerator": bool(
            _eddy_verdict is True and any(_j.get("applied") for _j in _acc_jumps)),
        # The same two numbers as eddy_warmup_resid / eddy_warmup_tol, under
        # the names the settle test itself uses — these are the pair the
        # sweep points and the UI carry (the eddy_warmup_* names stay for the
        # physics-regression pins and the existing Simulation payload).
        "eddy_settle_residual": (None if (_warm_resid is None
                                          or not math.isfinite(_warm_resid))
                                 else float("%.3g" % _warm_resid)),
        "eddy_settle_tol": float(_EDDY_SETTLE_TOL),
        # HOW the verdict was reached (2026-09-24): whole-period means per
        # conductor group (or a seed's same-angle reference), the per-group
        # residuals, the periods marched and whether the splices carried the
        # eddy state across the period with the pole-pair map.
        "eddy_settle_gauge": (dict(_warm_gauge) if _warm_gauge else None),
        # how σ·∂A/∂t was integrated in time: "bdf2" (default, 2nd order;
        # be_steps = the backward-Euler start steps of each march) or
        # "backward_euler" (SB_EDDY_BE=1).  None without the coupled solve.
        "eddy_time_scheme": (dict(_ed_ts) if eddy else None),
        # magnets bisected by the sector cut, paired into one ∫J=0 row each
        # (simulation/cut_bodies.py); None without the coupled solve
        "bisected_magnets": _cut_info,
        # the shaft skin-layer mesh spec this run was built with (None = the
        # wall was meshed by the CDT alone: rule off, or no rotor conductors)
        "shaft_skin_layer": _skin_info,
        # what a COLD march started from: the static (∂A/∂t = 0) field one
        # step before its first frame, or None (seeded / voltage / frozen-ν)
        "eddy_cold_start": _static_seed_info,
        # NO tau here on purpose.  The decay is not one exponential: on the
        # 150 mm the fit off the first frames reads 35 us and the fit off the
        # settled tail reads 680 us, while the transient actually needed ~0.4
        # ms to die — so a single "tau_est" would under-read the settling time
        # by 10x in exactly the direction that makes a run look trustworthy
        # when it is not.  The frame count and the residual are measured, not
        # fitted; those are what ship.  (The local fit is still logged, where
        # it is labelled for what it is.)
        # 2-D solved copper.  In delta the circulating loss is part of what the
        # winding dissipates, so it is IN the total, not a footnote beside it.
        "P_cu_total_solve_W": round(float(P_cu_solve_avg2) + float(_P_circ), 3),
        "P_cu_total_solve_2d_only_W": round(float(P_cu_solve_avg2), 3),
        "P_cu_dc_2d_solve_W": round(float(P_cu_dc2d_avg2), 3),
        "P_cu_ac_solve_W": round(float(P_cu_ac_avg2), 3) if eddy else 0.0,
        "P_cu_ac_prox_W": round(float(P_cu_ac_prox_avg2), 3),
        "P_mag_solve_W": (round(float(P_mag_avg2), 3)
                          if (eddy and rotor_eddy) else 0.0),
        "P_shaft_solve_W": (round(float(P_shaft_avg2), 3)
                            if (eddy and rotor_eddy) else 0.0),
        # 4 decimals, not 3: a 1 mm CFRP ring at ~80 S/m dissipates milliwatts,
        # and rounding it to 0.000 would read as "not solved".
        "P_sleeve_solve_W": (round(float(P_sleeve_avg2), 4)
                             if (eddy and rotor_eddy) else 0.0),
        # The conductor-body guard's summary (None on a non-eddy run): slots x
        # n_wires bodies as built, and the in-slot area spread with any
        # clipped-stack warnings — see check_eddy_conductor_bodies.
        "eddy_conductor_check": _eddy_con_check,
        # a LINEAR frequency-domain estimate (renamed from *_honest_W
        # 2026-09-27; contracts/adapters.py reads old records)
        "P_mag_linear_W": round(float(P_mag_prox_avg2), 3),
        "P_shaft_linear_W": round(float(P_shaft_prox_avg2), 3),
        # The window the two numbers above were solved on (rotor_window.py on
        # the nodal potential): method, q windows chained, harmonics solved.
        "P_rotor_eddy_honest_window": _P_rot_eddy_window,
        # AXIAL magnet segmentation — what `magnet_lamination` did to the two
        # magnet numbers above.  Always present (factor 1.0 + the measured loop
        # width on a solid magnet), so the card can say "solid" rather than say
        # nothing.  See simulation/losses.magnet_segmentation: it is a MODEL.
        "magnet_segmentation": _seg_rep,
        "P_fe_avg_W": round(float(P_fe_avg2), 3),
        "P_fe_terms": _fe_break,   # {stator|rotor: hyst/eddy/excess/k_f/model}
        "P_fe_raw_window_candidate_avg_W": P_fe_raw_window_candidate_avg2,
        "P_fe_detrended_candidate_avg_W": P_fe_detrended_candidate_avg2,
        "P_fe_surface_selected_candidate": (
            "raw_window_unfiltered" if _has_surface_candidates else None),
        # How the ROTOR iron window was closed (rotor_window.py): method,
        # q windows chained, total electrical periods, the rotor period read
        # off the data, or — closed False — why the open window was kept.
        "P_fe_rotor_window": _P_fe_rotor_window,
        "P_fe_rotor_open_window_diagnostic": P_fe_rotor_open_window_diag,
        "P_loss_total_avg_W": round(float(P_loss_avg2), 3),
        "P_airgap_W": P_airgap_avg2, "P_mech_avg_W": P_mech_avg2,
        "P_elec_in_W": P_elec_in2,               # ⟨Σ v·i⟩ (0 at no-load)
        # TERMINAL CONNECTION.  `connection` above groups a phase's coils
        # (series/parallel); this is how the three phases meet at the box.
        "star_delta": _sd_mode,
        # Which strand bonding actually applied.  NOT the argument: one wire in
        # hand has nothing to bond and no eddy solve has strand currents at
        # all, so the run says what it did rather than what it was asked.
        "strand_bonding": ("transposed" if (int(wire_parallel) <= 1 or not eddy)
                           else ("series" if _ed_paths is not None
                                 else ("parallel" if strand_group > 1
                                       else "transposed"))),
        "V_line_peak_solved_V": round(float(_V_ll_pk), 1),
        "V_line_rms_solved_V": round(float(_V_ll_rms), 1),
        "I_line_rms_A": round(float((_I_ph_rms_solved or I_phase_rms)
                                    * (math.sqrt(3.0) if _is_delta else 1.0)), 1),
        "line_current_factor": (math.sqrt(3.0) if _is_delta else 1.0),
        "line_voltage_factor": (1.0 if _is_delta else math.sqrt(3.0)),
        "L0_mH": (None if _L0_mH is None else round(_L0_mH, 6)),
        "P_cu_circulating_W": round(float(_P_circ), 1),
        "circulating_harmonics": _circ_rows,
        "circulating_note": (
            "" if not _is_delta else
            "delta only: the winding's zero-sequence triplen EMF driven round "
            "the closed loop against 3.Z0. L0 measured on this run's own iron "
            "at the operating point, rotor-angle averaged; R at h.f "
            "extrapolated from the solved AC extra as h^2 (proximity), which "
            "is the loosest step in it."),
        "R_phase_ohm": R_phase, "n_slip_nodes": int(Nring),
        # The CONNECTION's parallel paths, as the label says them.
        "n_parallel": int(n_parallel_conn),
        # …and the numbers that say what the coil actually is.  The slot holds
        # `num_wires_per_slot` wire rows of `wire_split` strips each;
        # `wire_parallel` rows are in hand per turn and a row's strips are
        # consecutive SERIES turns, so the coil has
        # `turns_per_coil` SERIES turns and the divider every quantity above was
        # solved with is `n_parallel_eff`.  Reported rather than derived
        # downstream: a consumer that recomputed it from the config could not
        # see a per-request geometry override.
        "wire_parallel": int(wire_parallel),
        "wire_split": int(wire_split),
        "turns_per_coil": int(turns_per_coil),
        "n_parallel_eff": int(n_parallel),
        **_dq,
        # The LABEL the paths came from, carried with them.  n_parallel alone
        # cannot be read back to a connection (2S-2P and 1S-2P both say 2), and
        # the card that shows these numbers has to name the winding they belong
        # to — "changed the connection, nothing moved" is unanswerable without
        # it.  Empty = no label, or a label that disagrees with the paths that
        # were actually solved (an explicit n_parallel wins over it) — naming it
        # then would be a lie, and no name is the honest answer.
        "connection": _conn_label_used,
        # which factorisation did the linear solves (2026-09-29): Cholesky
        # (PARDISO mtype 2) on the SPD-by-construction systems, the
        # unsymmetric LU (mtype 11) for the rest and for anything the run-time
        # checks declined.  Provenance only; no value depends on it beyond
        # solver round-off.
        "linear_solver": {"lu_solves": int(_p2.pardiso_solves),
                          "lu_analyses": int(_p2.pardiso_analyses),
                          "cholesky_solves": int(_p2.spd_solves),
                          "cholesky_analyses": int(_p2.spd_analyses),
                          "cholesky_declined": int(_p2.spd_declined),
                          "cholesky_failures": int(_p2.spd_failures)},
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
        "drive": _drv_name,
        # ── WHAT THE SOURCE APPLIED, per solved step ─────────────────────
        # Small on purpose: n_steps values per phase, sampled AT the solve
        # steps.  That is what the circuit actually integrated, so the ripple
        # in it lines up edge-for-edge with the torque and current series
        # beside it; the carrier-resolution waveform would be a prettier
        # picture of something the run did not solve.  Imposed-CURRENT sources
        # carry no arrays here at all — what they applied is already I_A/I_B/
        # I_C, and duplicating it would be two copies to disagree.
        "excitation": {
            "kind": _drv_name,
            "series": ("V" if _vdrive else "I"),
            "quantity": _desc.get("quantity"),
            "settle_periods": int(_v_settle_periods),
            **({"A": _vapp['A'], "B": _vapp['B'], "C": _vapp['C']}
               if _vdrive else {}),
        },
        "v_phase_peak_V": (_desc.get("v_phase_peak_V") if _vdrive else None),
        "v_delta_deg": (_desc.get("v_delta_deg") if _vdrive else None),
        # ── PWM inverter source (drive="pwm_voltage") ────────────────────
        # Built by PwmVoltageSource.describe().  The EFFECTIVE switching
        # frequency beside the requested one, because the carrier is snapped to
        # a whole number per electrical period (a run asked for 16 kHz on a
        # 1516.7 Hz fundamental actually switches at 16.68 kHz); the
        # steps-per-switching-period ratio, which is the single number that says
        # whether this run RESOLVED the ripple it is reporting (below ~10 the
        # exact-mean voltage integration averages the pulses and the reported
        # ripple is an under-estimate); the mixed-settle composition when it was
        # used; the edge-resolution pole and line waveforms; and the DC-link
        # series.  None on every source that is not an inverter.
        "pwm": _desc.get("pwm"),
        # ── imposed arbitrary current (drive="custom_current") ───────────
        "custom_current": _desc.get("custom_current"),
        # ── 120° six-step block commutation (drive="bldc_current") ──────
        # Every amplitude the comparison needs, together, because "the BLDC run
        # made more torque" is not a statement until it says at what current:
        # the flat top that was asked for, the rms it really carries (the
        # copper-loss number, i_block·√(2/3)) and the fundamental that produces
        # the mean torque (i_block·2√3/π).
        "bldc": _desc.get("bldc"),
        # Aitken settling anchor: attempts vs applications.  applied == 0 with
        # attempts > 0 means the guards skipped every one and the anchor did
        # nothing for this run — the measured state on the pinned machine.
        "v_anchor_attempts": int(_v_anchor_tries),
        "v_anchor_applied": int(_v_anchor_applied),
        # …and the DC-orbit solve (simulation/dc_orbit.py): every whole
        # settling period's exact flux drift and DC, the correction applied at
        # its end (None on the free verification period, and throughout under
        # SB_V_DC_SOLVE=0), the period Jacobian's eigenvalues, the turn-on
        # predictor where applied.  None when the run did not solve for the DC
        # (imposed current, the sinusoid's Δ² anchor, no whole settling
        # period).
        "v_dc_orbit": (None if _dc_orbit is None else _dc_orbit.describe()),
        # circuit-iteration convergence stats, one entry per frame of the
        # REPORTED window (settling frames stripped with every other series,
        # so the indices line up with I/psi/T) + the honest steady-state
        # quality gauge: the worst phase's mean current over the last WHOLE
        # electrical period (0 A on a converged periodic orbit).
        "v_drive_diag": (_v_diag if _vdrive else None),
        "v_dc_residual_A": (None if _dc_res is None else round(_dc_res, 3)),
        "v_dc_residual_phase": _dc_res_ph,
        "v_dc_residual_tol_A": _V_DC_RESIDUAL_TOL_A,
        # The verdict, not just the number: above the tolerance the run's
        # TORQUE RIPPLE and current ripple are the DC, not the machine.
        "v_dc_unconverged": (None if _dc_res is None
                             else bool(abs(_dc_res) > _V_DC_RESIDUAL_TOL_A)),
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


# the signature the transactional wrapper binds its arguments against
_SB_ONCE_SIGNATURE_OF = _fem_transient_sliding_band_once


def _public_sliding_band_signature():
    import inspect as _insp
    s = _insp.signature(_fem_transient_sliding_band_once)
    return s.replace(parameters=[p for p in s.parameters.values()
                                 if p.name != "_tdm_ctl"])


# callers and introspection see the solver's own parameters on the wrapper
fem_transient_sliding_band.__signature__ = _public_sliding_band_signature()


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
    sampling_purpose: SamplingPurpose = "standard",
    n_periods: float,
    gamma_deg: float,
    I_phase_rms: float,
    rpm: Optional[float] = None,     # mechanical speed [rpm]; None = the global
                                     # config's simulation.rpm (see
                                     # fem_transient_sliding_band)
    daxis_deg: Optional[float] = None,  # d-axis reference, GIVEN (None = measured;
                                     # see fem_transient_sliding_band)
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
    magnet_temp_c: Optional[float] = None,   # magnet temperature [°C]; None = the
                                     # assigned card exactly as the library quotes it
                                     # (see fem_transient_sliding_band)
    end_winding_factor: float = 0.0,
    rotor_eddy: bool = False,
    star_delta: str = None,          # "star" (default) | "delta" — how the three
                                     # phases meet at the terminal box
    strand_bonding: str = None,      # "transposed" (default) | "parallel" |
                                     # "series" (soldered ends) — how the
                                     # strands in hand are connected in the coupled
                                     # eddy solve; see fem_transient_sliding_band
    demag: bool = False,
    torque_filter: bool = False,  # deprecated compatibility option, ignored
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
    inc_ldq: bool = False,           # also measure the incremental (frozen-
                                     # permeability) d-q inductances of the
                                     # point — see frozen_permeability_ldq
    six_phase: Optional[dict] = None,  # two in-phase 3-phase sets (winding_sets);
                                     # None = 3 phases, unchanged
    drive: str = "current",          # "current" | "voltage" | "pwm_voltage" | "custom_current"
    v_phase_peak: float = 0.0,
    v_delta_deg: float = 0.0,
    v_bus: float = 0.0,              # pwm_voltage: DC link [V]
    v_bus_real: float = 0.0,         # inverter: the PHYSICAL link when v_bus is the model bus
    f_switch: float = 0.0,           # pwm_voltage: carrier [Hz] (snapped, see simulation/pwm.py)
    inverter_nonideal=None,          # inverter: the device's per-leg non-ideality (Controller)
    waveform=None,                   # custom_current: [(θ_e_deg, i_A)] over one electrical period
    i_block: float = 0.0,            # bldc_current: flat-top block amplitude [A terminal]
    excitation=None,                 # an ExcitationSource OBJECT instead of the five named
                                     # drives — an external controller / co-simulation
                                     # (simulation/excitation.py).  None = build it from
                                     # `drive` and the keywords above, as before.
    element_order: int = 2,          # 2 = P2, the only basis (see fem_transient_sliding_band)
    return_frames: int = 0,          # >0: also return N animation keyframes
    eddy: bool = False,              # coupled sigma*dA/dt eddy-current solve (the J-view physics)
    eddy_method: Optional[str] = None,  # "tdm" (default) | "march" | None (env/config)
                                     # — see fem_transient_sliding_band
    tdm_demag: Optional[str] = None,  # "full" (default) | "shortcut" | None (env)
    return_field: bool = False,      # ALSO return the LAST frame's field snapshot
                                     # (mesh + A + B + tags + Jeddy + loss_dens) under
                                     # result["field"].  No extra solve: it is the frame
                                     # the transient just finished, kept instead of thrown
                                     # away, so the field views can render the run's own
                                     # field instead of re-solving it.
    torque_method: Optional[str] = None,  # "coulomb" (default) | "hybrid_maxwell_ac" | None
    gap_refine: bool = True,         # re-solve ONCE with more gap layers per side when the
                                     # Coulomb self-check fails its 5 % gate (see below)
) -> Dict:
    """THE single canonical sliding-band transient invocation.

    Every consumer that needs a 2-D transient solve funnels through here — the
    Simulation route (get_fem_transient), the optimizer (refine_proc.run_one) and
    the solver.em_transient module — so the optimizer's physics can NEVER drift
    from what the Simulation tab shows. Pure: no caching, no global progress, no
    disk-save (those UI concerns stay in the route, which wraps this). Returns the
    raw sliding-band result dict (sbres).
    """
    _kw = dict(
        n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
        sampling_purpose=_sampling_purpose(sampling_purpose),
        gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
        rpm=(None if rpm is None else float(rpm)),
        daxis_deg=(None if daxis_deg is None else float(daxis_deg)),
        n_parallel=(None if n_parallel is None else int(n_parallel)),
        connection=(None if connection is None else str(connection)),
        mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
        outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
        n_sectors=int(n_sectors) if int(n_sectors) > 1 else -1,
        stator_fillet_mm=float(stator_fillet_mm),
        coil_temp_c=float(coil_temp_c),
        magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
        end_winding_factor=float(end_winding_factor),
        rotor_eddy=bool(rotor_eddy), strand_bonding=strand_bonding,
        star_delta=star_delta,
        demag=bool(demag),
        torque_filter=bool(torque_filter), pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=geo_mesh,
        component_mesh_mm=(component_mesh_mm or {}), geo_override=geo_override,
        progress_cb=progress_cb, hi_fidelity=bool(hi_fidelity),
        structured_gap=bool(structured_gap), airgap_macro=bool(airgap_macro),
        frozen_nu=bool(frozen_nu), inc_ldq=bool(inc_ldq),
        **({} if not six_phase else {"six_phase": dict(six_phase)}),
        drive=str(drive or "current"), v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg),
        v_bus=float(v_bus), v_bus_real=float(v_bus_real),
        f_switch=float(f_switch), waveform=waveform,
        i_block=float(i_block), inverter_nonideal=inverter_nonideal,
        excitation=excitation,
        element_order=int(element_order),
        return_frames=int(return_frames),
        eddy=bool(eddy),
        eddy_method=(None if eddy_method is None else str(eddy_method)),
        tdm_demag=(None if tdm_demag is None else str(tdm_demag)),
        # field_first is NOT set: the snapshot is the LAST frame, the one whose
        # B(t) history is complete, which is what the loss map and the coupled
        # eddy J are derived from.
        return_field=bool(return_field), torque_method=torque_method)
    return _solve_with_gap_refinement(_kw, gap_refine=bool(gap_refine))


def _solve_with_gap_refinement(kw: dict, gap_refine: bool = True) -> Dict:
    """Solve, then apply the MEASURED air-gap rule (owner 2026-09-30).

    The Coulomb torque is computed on the rotor-side and on the stator-side
    gap ring; their difference, over max(p-p, 0.5 % of |mean|), is the gap
    mesh's own error of the ripple.  Above ``SELF_CHECK_GATE`` (5 %) the run is
    solved ONCE more with the gap layers per side predicted by
    ``gap_layers_for_self_check`` (measured order 1.5, 4 % target, at most 4/side),
    on the SAME slip ring (so the rotor angles and the step snap do not move)
    — measured: the ring density does not move torque or ripple, the gap
    layers do (docs/COULOMB_TORQUE_2026-09-30.md §3.3, §6).  The second result
    is returned with ``gap_refinement`` describing both solves; a run that
    still fails keeps ``ripple_mesh_limited`` set.  Internal probes, the
    harmonic macro gap and ``SB_GAP_REFINE=0`` are never refined.
    """
    from motor_ai_sim.simulation.virtual_work_torque import (
        SELF_CHECK_GATE as _gate, gap_layers_for_self_check as _gl_for)
    res = fem_transient_sliding_band(**kw)

    def _sc(r):
        c = (r.get("coulomb_torque") or {}).get("layer_self_check") or {}
        return c.get("rel_to_ripple_scale")
    gl0 = float(res.get("gap_layers_effective") or kw.get("gap_layers") or 1.0)
    eps0 = _sc(res)
    new = None
    if (gap_refine and kw.get("sampling_purpose") != "internal_probe"
            and not bool(kw.get("airgap_macro"))
            and _os_sb.environ.get("SB_GAP_REFINE", "1") != "0"):
        new = _gl_for(gl0, eps0)
    info = {"applied": False, "gate_rel_to_ripple_scale": _gate,
            "gap_layers_per_side": gl0, "self_check_rel_to_ripple_scale": eps0}
    if new is not None and res.get("eddy_settled") is False:
        # An unsettled eddy warm-up leaves a transient in the reported window;
        # the two rings then disagree about the transient, not about the mesh
        # (measured: L155 at 2/side, warm-up capped at residual 21.6 %,
        # self-check 18 %, P_fe swinging 209 W).  A finer mesh cannot fix that.
        info["skipped_reason"] = ("eddy warm-up not settled: the self-check "
                                  "measures the remaining transient, not the gap mesh")
        new = None
    if new is None:
        res["gap_refinement"] = info
        return res
    log.warning("SB gap rule: Coulomb self-check %.1f %% of the ripple scale at "
                "%g gap layers/side > %.0f %% gate — re-solving once at %g/side "
                "on the same %s-node slip ring", 100.0 * eps0, gl0,
                100.0 * _gate, new, res.get("slip_nodes_per_period"))
    kw2 = dict(kw, gap_layers=float(new),
               slip_per_period=int(res.get("slip_nodes_per_period") or 0) or None)
    res2 = fem_transient_sliding_band(**kw2)
    res2["gap_refinement"] = dict(
        info, applied=True, gap_layers_per_side=float(new),
        self_check_rel_to_ripple_scale=_sc(res2),
        first={"gap_layers_per_side": gl0, "self_check_rel_to_ripple_scale": eps0,
               "T_avg_Nm": res.get("T_avg_Nm"),
               "T_ripple_pp_Nm": res.get("T_ripple_pp_Nm"),
               "solve_wall_s": res.get("solve_wall_s")})
    res2["gap_layers_requested"] = res.get("gap_layers_requested")
    res2["gap_layers_note"] = (
        "gap layers raised from %g to %g per side: the Coulomb self-check was "
        "%.1f %% of the ripple scale (gate %.0f %%)"
        % (gl0, new, 100.0 * eps0, 100.0 * _gate))
    return res2


def measure_six_phase_inductances(*, six_phase: dict, I_phase_rms: float,
                                  gamma_deg: float, n_samples: int = 4,
                                  **kw) -> Dict[str, Any]:
    """L_xy and the per-set L_d/L_q of a six-phase winding, on the FULL ring.

    The x-y mode drives the sets against each other; with the owner's default
    split (set 1 = the first half of the paths) that current pattern does not
    repeat with any sector, so a sector model cannot represent it.  This is one
    short magnetostatic sine-current run of the whole machine at the duty's
    operating point (``I_phase_rms``, ``gamma_deg``, the caller's temperatures
    and mesh in ``kw``) — ``n_samples`` rotor positions over half an electrical
    period, every one probed — no eddy, no voltage circuit: the inductance is
    the frozen-permeability derivative of the loaded magnetostatic field.

    Returns the ``six_phase`` block of that run (``inductances`` + the source
    split check), plus what was solved.
    """
    ns = max(2, int(n_samples))
    kw = dict(kw)
    for k in ("eddy", "drive", "inc_ldq", "n_sectors", "return_field",
              "return_frames", "n_steps_per_period", "n_periods", "six_phase"):
        kw.pop(k, None)
    res = em_transient_eval(
        n_steps_per_period=2 * ns, n_periods=0.5, gamma_deg=float(gamma_deg),
        I_phase_rms=float(I_phase_rms), n_sectors=-1, inc_ldq=True,
        drive="current", eddy=False, six_phase=dict(six_phase),
        sampling_purpose="internal_probe", **kw)
    blk = dict(res.get("six_phase") or {})
    blk["probe"] = {"model": "full ring", "frames": ns,
                    "I_phase_rms_A": float(I_phase_rms),
                    "gamma_deg": float(gamma_deg),
                    "picard_converged": bool(res.get("picard_converged", False)),
                    "T_avg_Nm": res.get("T_avg_Nm")}
    inc = res.get("inc_ldq") or {}
    if inc.get("Ld_mH") is not None:
        blk["probe"]["Ld_machine_mH"] = inc.get("Ld_mH")
        blk["probe"]["Lq_machine_mH"] = inc.get("Lq_mH")
    return blk
