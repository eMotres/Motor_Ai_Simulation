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
from typing import Dict, List, Mapping, Tuple, Optional

import numpy as np

from motor_ai_sim.simulation.pardiso_lifetime import (
    own_pardiso as _own_pardiso, pardiso_scope as _pardiso_scope,
)

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


def psipm_cache_key(geo, wind, connection=None) -> str:
    """The disk key ``noload_psi_pm`` files its answer under.

    Its own function so the "this cache cannot answer for another winding"
    property is testable without solving anything.

    ψ_PM DOES depend on the CONNECTION — not through the current (there is none)
    but through n_series: the phase flux linkage is the SUM of the series coil
    groups, so 4S links four times what 4P links.  The first version of this key
    argued "I = 0, the connection cannot matter" and cached one number for all
    connections; a 4S run would then have divided its 4S-scaled ψd against a 4P
    ψ_PM and shipped a silently wrong Ld.  (Caught by the user asking "а Winding
    Connection ты учёл?" — reviewed, measured, fixed before it produced a
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
    return "psipm_v2_%s_L%s_C%s_%s_%s" % (
        _fp, int((wind or {}).get("layers", 1) or 1), _conn_pm or "cfg",
        _winding_cache_identity(geo, wind), _scale)


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
    ]))
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
    key = psipm_cache_key(geo, wind, connection)
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
    document does too — *«Ld/Lq нужно указывать тоже для 20 градусов и без
    тока, как для KV»*.

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
    key = "ldq0_v1_%s_M%g" % (psipm_cache_key(geo, wind, connection),
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
        # thickened the shaft wall ("зачем её калибровать, если я только
        # увеличил толщину вала?", 2026-09-07).
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
# ── THE SETTLE SCHEDULE NOW BELONGS TO THE SOURCE ────────────────────────────
# Settling PERIODS marched (and discarded) before the reported window on every
# imposed-VOLTAGE run — sinusoid or PWM — because the phase currents are circuit
# state with an L/R start-up transient.  Every knob that used to live here is a
# field of `excitation.SettlePolicy` now, read off `source.settle_policy()`:
#
#   periods_static       SB_V_SETTLE_PERIODS  — 10 for the sinusoid (every
#                        pinned number was produced with it), 2 for PWM, 0 for
#                        an imposed-current source; an EXPLICIT env value wins
#                        for every source.  Never lower it in production: a
#                        fine-step PWM study costs 11x its reported window in
#                        settling, and whether a shorter settle buys anything is
#                        an empirical question (compare v_dc_residual_A and the
#                        reported metrics at 2 vs 10 before trusting one).
#   adaptive_tau_mult    SB_V_SETTLE_TAU_MULT — periods = ceil(k·τ_e/T_e) with
#   periods_cap          SB_V_SETTLE_MAX        τ_e = max(Ld,Lq)/R from the
#                        phasor initialiser, floored at periods_static, capped.
#   coarse_settle        SB_PWM_COARSE_SETTLE — mixed coarse/fine settle.
#   fine_settle_periods  SB_PWM_FINE_SETTLE   — WHOLE fine settling periods at
#                        the end of that prefix; the window the DC anchor
#                        measures the switching turn-on DC over (B5).
#   aitken               Δ² period-boundary flux anchors (the sinusoid only).
#   dc_anchor            period-mean DC anchor (PWM) — no knob, it is the fix.
#
# The MATH driven by those fields — _build_schedule, the adaptive block after
# the phasor initialiser, the two anchors in the frame loop — lives here; only
# the decision of WHICH numbers to feed it moved.  See simulation/excitation.py.

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
# the settle is now whole PERIODS so the period-mean anchor can measure that DC
# and remove it; two of them take 40 A to under 0.5 A.  (B5 / PWM study
# 2026-09-13; before it, this scheme's torque ripple read 55 % against the
# machine's own 0.9 %.)
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
    fixture) the aliased ripple walks into it and the anchor built on it
    over-corrected by 1.8x.  (B5 / PWM study 2026-09-13.)
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
# User, 2026-09-06: "мы же уже договаривались, что проход демагнитизации
# делается для каждого sweep только один раз; изменения геометрии небольшие, и
# каждый следующий расчёт берётся из предыдущего."  Sweep points had gone from
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
            return {"meta": {"nspp": int(z["nspp"]), "npd": float(z["npd"]),
                             "conn": str(z["conn"]), "temp": float(z["temp"]),
                             "mscale": float(z["mscale"])},
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
    if wc.get("meta") != wmeta:
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


@_pardiso_scope
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
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    geo_override: dict = None,
    eddy: bool = False,          # opt-in: time-coupled σ·∂A/∂t eddy-current solve
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
    structured_gap: bool = False,    # ANSYS-style concentric-ring air-gap mesh (experimental
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
    f_switch: float = 0.0,           # pwm_voltage: carrier frequency [Hz], SNAPPED to a whole
                                     # number of carriers per electrical period (synchronous PWM —
                                     # see simulation/pwm.py); the effective value is reported.
    waveform=None,                   # custom_current: [(θ_e_deg, i_A)] samples of the phase-A
                                     # TERMINAL current over one electrical period (B/C = the same
                                     # shape shifted ∓120°el), linearly interpolated.
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
    # geo mesh builds the 1/N wedge directly, but the sector ripple is still WIP.
    # This used to answer a 1/N request by silently solving the FULL RING —
    # "correct, just slower" — which meant the Mesh tab's 1/4 never actually ran
    # as 1/4 for anyone with the geo mesh on (the default): the user chose the
    # sector FOR ITS SPEED and paid full-disk time anyway, with one info log as
    # the only witness ("почему всё сбрасывается на full", 2026-08-22).  A 1/N
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
    #       bracket (user 2026-09-11: "делай, нужно точно знать").
    #
    # `None` DERIVES it from the geometry, and that is the default because the
    # geometry already decides: `wire_parallel` = k wires in hand, and a coil
    # wound with k of them is soldered at its two ends — there is no third
    # possibility to offer (user 2026-09-11: "соединение жил в руке у нас в
    # геометрии выбирается, не надо делать селектор").  k = 1 has nothing to
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
    # true q-axis and γ equals the physical current angle from the q-axis (=ANSYS
    # el_deg).  Cached per topology; the I=0 calibration run is recursion-guarded.
    # …and it is REPORTED while it runs.  On a geometry the cache has not seen
    # it is a 24-frame no-load solve — measured 39 s on the 200 mm 24s/28p —
    # during which the progress bar showed nothing at all, so pressing Run
    # looked like pressing nothing.  It is not overhead to hide: it is what
    # gives the user's γ a zero to be measured from.
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
                with open(_dp_chk) as _f_dx:
                    for _k2, _v2 in (_json_dx.load(_f_dx) or {}).items():
                        if isinstance(_k2, str) and _k2.startswith(_pref) and isinstance(_v2, (int, float)):
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
        daxis_eff = _resolve_daxis_shift(p, geo, wind, pole_pairs, geo_override,
                                         n_sectors, progress_cb=progress_cb)
        _daxis_src = "calibrated"

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
        waveform=waveform, i_block=float(i_block))
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
                        "torque); the run is at the SNAPPED resolution",
                        _req_steps, n_steps_per_period, _nodes_per_period)
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
    ms, ts, cs, mr, tr, cr = _build_sliding_band_meshes(
        polys, 0.0, mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, band_thickness_mm=0.4,
        n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0,
        gap_layers=gap_layers,
        component_mesh_mm=component_mesh_mm,
        full_ring=_full_ring, pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=_geo_mesh_eff)
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
            # "coil_<i>_slot<j>_<phase><+|->" — j identifies the SLOT, and
            # build_winding_layout puts a single-layer coil's two sides in
            # slots 2c and 2c+1, so c = j//2 names the physical COIL.  Nothing
            # else in this solver needed to know which conductor belongs to
            # which coil; the series strand paths do.
            try:
                _slot_j = int(nm.split("_slot", 1)[1].split("_", 1)[0])
            except Exception:
                _slot_j = -1
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
                "slot": _slot_j,
                "coil": (_slot_j // 2 if _slot_j >= 0 else -1),
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
    # User 2026-09-10: *"для электромагнитного анализа надо ещё рассчитывать
    # среднее поле в зазоре и писать это число в таблицу"*.  The mean |B| over
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
            and _wc_seed.get("br_val") is not None and _mag_idx.size):
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
    # The COUNT is the source's (`_settle.periods_static`, adapted below); the
    # schedule maths is the solver's and is unchanged.
    _n_periods0 = float(n_periods)

    def _build_schedule(_settle_p):
        n_periods = float(_n_periods0)
        n_total = max(1, int(round(n_steps_per_period * n_periods)))
        _v_settle_periods = 0        # imposed-current runs have no settle
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
        # period-mean DC anchor.  Only periods of UNIFORM resolution go in: the
        # mean is a measurement of the DC only if every frame of it rode the
        # same orbit.  (B5 / PWM study 2026-09-13.)
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
            # marched WHOLE and fine, and the period-mean anchor below takes
            # their DC out exactly.  (B5 / PWM study 2026-09-13.)
            for _pi in range(_settle_periods_total):
                _isf = (_pi >= _settle_periods_total - _fine_settle)
                for _j in range(_v_nspp if _isf else _c_nspp):
                    _sched_th.append(_th)
                    _sched_dth.append(_dth_f if _isf else _dth_c)
                    _sched_dt.append(_dt_f if _isf else _dt_c)
                    _sched_fine.append(_isf)
                    _th += (_dth_f if _isf else _dth_c)
                # End of this settling period.  Recorded as the index of its
                # LAST frame, so the test in the loop is a membership check
                # instead of a modulo that no longer holds.  Every period here
                # is whole AND of uniform resolution, so both anchors may look
                # at it — the Δ² flux one only where the source still asks for
                # it (the sinusoid; it is off on PWM since B5).
                if _settle.dc_anchor:
                    _dc_win[len(_sched_th) - 1] = len(_sched_th) - (
                        _v_nspp if _isf else _c_nspp)
                if _settle.aitken:
                    _settle_bounds.add(len(_sched_th) - 1)
            _fine_frames = _fine_settle * _v_nspp   # fine frames in the prefix
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
            if _settle.aitken and _v_nspp > 0:
                _settle_bounds = {k for k in range(n_total)
                                  if (k + 1) % _v_nspp == 0 and (k + 1) < _vskip}
            # …and the whole settling periods the DC anchor measures over —
            # every discarded period, the demag settling one included (it is a
            # whole electrical period too, and it sits between the last anchor
            # and the reported window).
            if _settle.dc_anchor and _v_nspp > 0:
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
    _S = _build_schedule(_settle.periods_static)
    (_vskip, _v_nspp, _v_settle_periods, n_periods, n_total, _dmskip,
     period_mech, dt, _fine_frames, _c_nspp, _settle_bounds, _sched_mixed,
     _sched_th, _sched_dth, _sched_dt, _sched_t, _sched_fine, _dc_win,
     _vskip_periods, _progress_comp) = [_S[_k] for _k in _SCHED_KEYS]

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
        _pardiso2 = _own_pardiso(_pypard2.PyPardisoSolver())
    except Exception as _pae:
        log.info("pypardiso unavailable (%s) — using SuperLU for P2", _pae)
        _pardiso2 = None
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
    _ed_con = []             # constrained bodies: dicts(key, tag, g, S, …)
    _ed_paths = None         # series strand paths (strand_bonding="series")
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

        # ── STRANDS IN HAND: one U per GROUP, not per strand ───────────────
        # Each conductor above carries its own ∫J = I row, which is the
        # PERFECTLY TRANSPOSED winding: every strand is forced to the same
        # current whatever flux it links.  A real k-in-hand coil is soldered at
        # its ends, so the strands are in PARALLEL — they share a voltage and
        # the flux-linkage difference between the rows drives a circulating
        # current between them (user 2026-09-11: "мы будем спаивать концы жил
        # вместе... там могут возникнуть компенсационные токи").
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
        # A merged group owns several tags: its U paints every element of all
        # of them (the strands share one U by construction).
        # NB `_t` is this module's alias for `time` — never a loop variable here.
        _ed_uloc = [np.concatenate([_ed_loc_by_tag[int(_tg_i)]
                                    for _tg_i in _c.get("tags", [_c["tag"]])])
                    for _c in _ed_con]
        _ed_gmask = {_k: (_ed_key_e == _k)
                     for _k in ("cu", "mag", "shaft", "sleeve")}

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
    # which the Aitken anchor and the handover DC removal deliberately move off
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
    # …and how many PERIOD-MEAN DC anchors fired (B5).  A different measurement
    # from the Δ² one and a different counter, so a run states which of the two
    # actually moved its state.
    _dc_anchor_applied = 0
    _dc_last = None          # (dc vector, scale) of the previous DC anchor
    _dc_scale = 1.0          # learned ψ-correction gain (see the anchor)

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

    # ── frame loop ───────────────────────────────────────────────────────
    _T2 = []; _psiA = []; _psiB = []; _psiC = []; _tt = []
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
    _inc_at: set = set()
    if inc_ldq and n_total > 0:
        _ns_inc = max(1, min(int(_INC_LDQ_SAMPLES), int(n_total)))
        _inc_at = {int(round(_j * n_total / _ns_inc)) % int(n_total)
                   for _j in range(_ns_inc)}
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
    _warm_ks: List[int] = []        # frame index of each sample (for angles)
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
    # User, 2026-09-05: "второй расчёт всегда отличается от первого".  Two
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
    # "проход демагнитизации делается для каждого sweep только один раз".  When
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
    _Ued = np.zeros(len(_ed_con))         # per-body conductor voltages
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
    _wc_Ued = None
    _warm_ref = None      # previous run's per-frame solid loss (same angles)
    if eddy and not _vdrive and n_total >= 8 and _wc_seed is not None:
        _wc = _wc_seed
        try:
            from scipy.spatial import cKDTree as _KDT
            _widx = _KDT(_wc["doflocs"]).query(
                np.asarray(b2.doflocs.T, dtype=np.float32), k=1)[1]
            _Aed_prev = np.asarray(_wc["A"], float)[_widx]
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
            _warm_seeded = True
            log.info("P2 eddy warm cache: eddy history seeded from the "
                     "previous run (%d → %d dofs; parent %.4g A / %.4g deg%s)",
                     len(_wc["A"]), N2, float(_wc["I"]), float(_wc["gam"]),
                     ", sweep mode" if _seed_from_previous() else "")
        except Exception as _wce:   # a bad seed must never fail the run
            _warm_seeded = False
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
                    _Aed_prev = np.zeros(N2)
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
                if k >= _vskip - 1:
                    _warm_done = True
                    if len(_warm_solid) >= 3:
                        _warm_resid, _warm_tau_s = _eddy_settle_resid(
                            _warm_solid, _ve_gauge_nspp, _ve_gauge_dt)
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
                                            and not _ve_gauge_carrier_ok)
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
                if k < 0:
                    _n_warm += 1          # every frame at θ<0 is warm-up
                _warm_solid.append(
                    (_pg.get("mag", 0.0) + _pg.get("shaft", 0.0)
                     if ("mag" in _Msig_grp or "shaft" in _Msig_grp)
                     else _pg.get("cu", 0.0)) * _wsc)
                _warm_ks.append(int(k))   # its electrical angle ≡ k mod n_total
                # Decision point: the probe's third sample IS frame 0; an
                # extension march decides at its own last frame (θ = −dθ),
                # where it already has a whole period of samples.
                if (k == -1) if _warm_extended else (k >= 0):
                    _warm_resid, _warm_tau_s = _eddy_settle_resid(
                        _warm_solid, int(n_steps_per_period), dt)
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
                    _quiet = (_warm_quiet
                              and not (demag and _dm_moved_in_warm))
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
                        _dm_moved_in_warm = False   # a fresh window judges fresh
                        _Aed_prev = A2.copy()
                        log.info("P2 eddy warm-up: not settled — extending by "
                                 "one electrical period (%d frames)", _eddy_cap)
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
                        if k >= 0:
                            _n_warm += 1            # this frame is re-solved
                        _fseq[_fi:_fi] = (list(range(-_dm_pre_len, 0))
                                          + ([0] if k >= 0 else []))
                        _Aed_prev = A2.copy()
                        log.info(
                            "P2 demag %s: %d frame(s) at θ<0 with the Br "
                            "ratchet ACTIVE, on the settled eddy field (the "
                            "%d warm-up frame(s) ran it FROZEN).",
                            ("ratchet unfrozen at the handoff (pre-pass "
                             "SKIPPED — seeded magnet)" if _dm_seeded
                             else "pre-pass"), _dm_pre_len, _n_warm)
                        continue
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
                    _Uel[_ed_uloc[_ci]] = float(_Ued[_ci])
                _Eq = -np.asarray(_ed_basis.interpolate(_dAe)) + _Uel[:, None]
                _ed_dens_hist.append(
                    _ed_sig_e * np.sum(_Eq ** 2 * _ed_dx, axis=1)
                    / np.maximum(_ed_area, 1e-30))
            _Aed_prev = A2.copy()
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
        Tq = _torque2(A2) * NS
        _T2.append(Tq)
        _pa, _pb, _pc = _psi2(A2)
        _psiA.append(_pa); _psiB.append(_pb); _psiC.append(_pc)
        _IA.append(Ist['A']); _IB.append(Ist['B']); _IC.append(Ist['C'])
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
        # The MEASUREMENT the source gets on the next frame — the machine's own
        # converged values, before any settling bookkeeping moves them.
        _fb_i_prev = {'A': Ist['A'], 'B': Ist['B'], 'C': Ist['C']}
        _fb_psi_prev = {'A': _pa, 'B': _pb, 'C': _pc}
        # Nominal elapsed time of this frame.  A LIST, not k*dt: the settle
        # frames can be coarser than the reported ones, so elapsed time is a
        # cumulative sum of the schedule (identical to k*dt when uniform).
        _tt.append(_sched_t[k] if k >= 0 else k * dt)
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
            # ── PERIOD-MEAN DC ANCHOR (B5 / PWM study 2026-09-13) ────────
            # The phasor init lands NEAR the orbit, not on it, and the
            # modulator turning on kicks the state off it again by about half
            # the ripple amplitude.  Both leave a stationary-frame DC that
            # decays with τ_e — 27 electrical periods on the L155, measured
            # from the settle itself (0.9637 per period) — so neither a 12-
            # period settle nor a 2-carrier pre-roll sheds it: the reported
            # window opened with 43 A of DC on a 433 A fundamental, and that
            # DC is the reported torque ripple.
            #
            # It does not have to be waited out; it can be MEASURED.  On a
            # periodic orbit ∮dψ = 0, so over a whole electrical period the
            # line-to-line equations give R·⟨i_A−i_B⟩ = ⟨v_A−v_B⟩ — and the
            # applied volt-seconds over a whole period are exactly zero for
            # the sinusoid AND for the synchronous regular-sampled PWM
            # (measured on every study run: 0.0000 V).  With Σi = 0 that makes
            # ⟨i_ph⟩ over a whole period the DC error itself, with no
            # interpolated reference to leak ripple into it — which is what
            # made the 2026-09-02 handover estimate over-correct.
            #
            # Take it out of the circuit STATE, exactly as the Aitken anchor
            # does, with the frame's own ∂ψ/∂i columns (i_C = −i_A−i_B, so two
            # columns; the anchor only ever fires at a whole electrical period,
            # where the machine is back in the same magnetic position):
            #   ψ_prev −= s·Σ_j (∂ψ/∂i_j)·dc_j ;  i_state −= s·dc
            #
            # s is LEARNED, not assumed.  The linearised response says s = 1
            # exactly, and it is not: s = 1 left 11.6 A on the 30 mm fixture,
            # the DC returning with its sign flipped and ~60 % of its size, i.e.
            # the state jump meets an effective inductance smaller than the
            # columns say (the eddy history is not corrected with them).  So
            # each anchor watches what the previous one achieved —
            # g = (dc_n − dc_{n+1})·dc_n / (s_n·|dc_n|²) — and uses s = 1/g next
            # time, clamped to [0.2, 3].  Measured, same fixture at a brutal 1.4
            # steps per carrier: 11.6 A with s = 1, 0.48 A with the learned s.
            # That is also why the mixed schedule ends in TWO whole fine
            # periods: the first anchor is the one that pays for the lesson.
            if _settle.dc_anchor and k in _dc_win:
                _k0 = int(_dc_win[k])                   # first frame of the period
                _off = len(_IA) - 1 - k                 # list index of frame k
                _w = np.asarray(_sched_dt[_k0:k + 1], float)
                _dc_p = {_ph: _period_dc(_ser, _off + _k0, _off + k, _w)
                         for _ph, _ser in (('A', _IA), ('B', _IB), ('C', _IC))}
                _dc_v = np.array([_dc_p['A'], _dc_p['B']], float)
                # What the PREVIOUS anchor achieved, and the gain that would
                # have zeroed it — clamped, believed only when the correction
                # moved the DC the way it was meant to, and only when there was
                # a DC worth measuring.  That last guard is the Δ² anchor's
                # lesson: once the boundary is at noise this quotient divides
                # noise by noise and walks to the clamp (measured, 280-step
                # L155: a 0.37 A coarse boundary asked for s = 3), and the
                # scale it learns there is the one the FINE anchors inherit.
                if _dc_last is not None:
                    _p0, _s0 = _dc_last
                    _den = _s0 * float(_p0 @ _p0)
                    _num = float((_p0 - _dc_v) @ _p0)
                    if (_den > 1e-9 and _num > 1e-9
                            and float(_p0 @ _p0) > (5.0 * _V_DC_RESIDUAL_TOL_A) ** 2):
                        _dc_scale = float(min(3.0, max(0.2, _den / _num)))
                _dA, _dB = _dc_scale * _dc_v[0], _dc_scale * _dc_v[1]
                # THIS frame's ∂ψ/∂i (eddy reaction included) when the Newton
                # left them; the phasor initialiser's small-signal columns only
                # as a fallback.
                _qaL = getattr(_drv, "last_qa", None)
                _qbL = getattr(_drv, "last_qb", None)
                if _qaL is None or _qbL is None:
                    _qaL, _qbL = _qa, _qb
                _psi_prev = {_ph: _psi_prev[_ph] - (_qaL[_i] * _dA + _qbL[_i] * _dB)
                             for _i, _ph in enumerate(('A', 'B', 'C'))}
                _iv_state = {_ph: _iv_state[_ph] - _dc_scale * _dc_p[_ph]
                             for _ph in ('A', 'B', 'C')}
                _dc_last = (_dc_v, _dc_scale)
                _dc_anchor_applied += 1
                _v_diag.setdefault("dc_anchor_A", []).append(
                    [round(_dc_p[_ph], 4) for _ph in ('A', 'B', 'C')]
                    + [round(_dc_scale, 3)])
                log.info("P2 vdrive period-mean DC anchor at frame %d (%s period "
                         "%d frames): iA %+.3f iB %+.3f iC %+.3f A measured, "
                         "scale %.3f", k, "fine" if _sched_fine[k] else "coarse",
                         k + 1 - _k0, _dc_p['A'], _dc_p['B'], _dc_p['C'],
                         _dc_scale)
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
                    for _tg_i in _c.get("tags", [_c["tag"]]):
                        _u_n[_bdofs(_elm[_tg_i])] = float(_Ued[_ci])
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
                 _v_diag["iters"], _v_diag["resid"],
                 _vapp['A'], _vapp['B'], _vapp['C'])
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
                "terms are barely touched by it.  %d period-mean DC anchor(s) "
                "fired.", _dc_res, _dc_res_ph, _V_DC_RESIDUAL_TOL_A,
                _dc_anchor_applied)
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
    # ── Publish the warm cache for the NEXT run ───────────────────────────
    # The seed field was stashed mid-march (k = n_total−3); the same-angle
    # solid-loss reference needs the WHOLE measured window, so it is built
    # here from the per-frame lists (same expression and _wsc scaling as the
    # settle gauge's samples).
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
                _warm_cache_store({
                    "doflocs": np.asarray(b2.doflocs.T,
                                          dtype=np.float32).copy(),
                    "A": _wc_A, "Ued": _wc_Ued,
                    "solid": np.asarray(_solid_ref, float),
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
            if _ed_sl and "sleeve" in _Msig_grp:
                P_sleeve_ser2 = list(_ed_sl)
                P_sleeve_avg2 = float(np.mean(_ed_sl))
            log.info("EDDY-SOLVE(P2) magnet=%.3f shaft=%.3f sleeve=%.4f W vs "
                     "honest frequency-domain magnet=%.3f shaft=%.3f W",
                     P_mag_avg2, P_shaft_avg2, P_sleeve_avg2, P_mag_prox_avg2,
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
    P_tot_ser2 = [c + f + m + s + sl for c, f, m, s, sl in
                  zip(P_cu_ser2, P_fe_ser2, P_mag_ser2, P_shaft_ser2,
                      P_sleeve_ser2)]
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
    # Legacy hybrid: fundamental space-vector mean plus raw Maxwell AC.
    # This is not general virtual work; see sb_postproc.hybrid_torque and
    # docs/solver-torque-validation-plan.md for its assumptions and open issues.
    _torque_method = "maxwell_stress"
    try:
        _T2, _torque_method = _hybrid_torque(
            _psiA, _psiB, _psiC, _IA, _IB, _IC, _T2raw, pole_pairs,
            n_parallel=int(n_parallel))
    except Exception as _te:
        log.warning("P2 hybrid torque failed (%s) — using Maxwell series", _te)
    T_arr = np.asarray(_T2, float)
    Tavg = float(T_arr.mean()) if T_arr.size else 0.0
    _T_report, Trip_raw = torque_metrics(_T2)
    _omega_m2 = 2.0 * math.pi * rpm / 60.0
    P_airgap_avg2 = float(Tavg * _omega_m2)
    P_mech_avg2 = P_airgap_avg2 - (P_fe_avg2 + P_mag_avg2 + P_shaft_avg2
                                   + P_sleeve_avg2)
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
    #
    # TRUNCATION ORDER.  This was a hard `min(5, …)`, which contradicted the
    # docstring above it: the SLOT ripple of the back-EMF sits at k_slot±1
    # (11 and 13 on 24s/28p), so a cap of 5 dropped exactly the content the
    # comment promised to keep, and every reported voltage waveform came out
    # harmonic-free above the 5th — an impossible spectrum for an FE solve.
    # Measured against ANSYS on the 150 mm machine at no load (2026-08-22):
    # ours h7/h9/h11/h13 = 0.30/1.17/0.22/0.18 % of fundamental vs ANSYS
    # 0.80/1.00/0.28/0.26 %, and the spectrum is STABLE from k=13 all the way
    # to Nyquist (k=29 on 60 frames) — the quantisation floor the truncation
    # was guarding against is not there at usable step counts.
    # So keep through the SECOND slot harmonic (physics, not a magic number)
    # and clamp below Nyquist; the guard band only ever binds on short runs.
    _k_slot = max(1, math.lcm(int(p.num_slots), int(p.num_poles))
                  // max(1, int(pole_pairs)))       # cogging order / elec period
    _Kv2 = max(1, min(2 * _k_slot + 1, (int(n_total) // 2) - 1))

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
                        np.asarray(_Pro_z.T @ f_coil2['A']).ravel()[_free_z]]))
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
            _w_e = 2.0 * math.pi * float(f_elec)
            _Iph2 = (float(_I_ph_rms_solved or I_phase_rms) ** 2) or 1.0
            _Rac1 = (float(P_cu_ac_avg2) / (3.0 * _Iph2)) if eddy else 0.0
            for _h in (3, 9, 15):
                if _h >= _Fz.size:
                    break
                _Eh = _h * _w_e * float(_Fz[_h]) / math.sqrt(2.0)   # V rms
                if _Eh <= 0.0:
                    continue
                _Rh = float(R_phase) + _Rac1 * (_h ** 2)
                _Zh = math.hypot(_Rh, _h * _w_e * _L0)
                _Ih = _Eh / max(_Zh, 1e-12)
                _Ph = 3.0 * _Ih * _Ih * _Rh
                _P_circ += _Ph
                _circ_rows.append({"harmonic": _h, "E_rms_V": round(_Eh, 2),
                                   "R_ohm": round(_Rh, 6),
                                   "X_ohm": round(_h * _w_e * _L0, 5),
                                   "I_circ_rms_A": round(_Ih, 2),
                                   "P_W": round(_Ph, 1)})
            log.info("%s connection: L0 = %.5g mH (rotor-angle mean, run's own "
                     "iron), circulating %s -> %.0f W added to the copper",
                     _sd_mode.upper(), _L0_mH,
                     ", ".join("h%d %.1f A" % (r["harmonic"], r["I_circ_rms_A"])
                               for r in _circ_rows) or "none",
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
        "dc_anchors": int(_dc_anchor_applied),
        "v_nspp": int(_v_nspp), "dc_link": _dc_link,
        "settle_periods": int(_v_settle_periods), "n_parallel": int(n_parallel),
    }
    try:
        _desc = _src.describe(_desc_ctx) or {}
    except TypeError:       # a source whose describe() takes no context
        _desc = _src.describe() or {}
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
    # ── Demag aggregate for the summary card ─────────────────────────────────
    # ONE number an engineer can act on: the AREA-weighted mean Br the magnets
    # kept.  To first order (T ∝ ψ_pm ∝ ∫Br dA) its deficit bounds the torque /
    # EMF loss, which is what "коэффициент демагнитизации" should mean — the
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
            "br_worst_pct": round(100.0 * float(_brm.min()), 1),
            "area_derated_pct": round(100.0 * float(
                np.sum(_ar_mag[_brm < 0.999]) / _wsum), 2),
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
        _dq = {
            "psi_d_Wb": round(_psid, 6), "psi_q_Wb": round(_psiq, 6),
            "i_d_A": round(_idm, 2), "i_q_A": round(_iqm, 2),
            "T_dq_Nm": round(_Tdq, 3),
            "dq_torque_check_pct": (None if _chk is None else round(_chk, 2)),
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

    return {
        "method": "sliding_band_p2", "element_order": 2,
        **({"inc_ldq": _inc} if _inc else {}),
        # WHICH ELECTRICAL FRAME THIS RUN WAS SOLVED IN.  gamma is measured
        # from the q-axis, and the q-axis is wherever the d-axis calibration
        # put it — so a run whose calibration landed on the wrong sample of
        # psi_A solves a DIFFERENT operating point than the gamma on screen,
        # with nothing in the result to say so.  It cost an hour of bisection
        # to recover this number for one run by fitting T and V_peak against a
        # gamma sweep; it is one float, and it belongs in every payload.
        "daxis_deg": round(float(daxis_eff), 4),
        "daxis_source": _daxis_src,
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
        "solve_wall_s": round(float(_t.time() - t0), 1),
        # What the caller ASKED for, beside what actually ran.  The whole-node
        # snap silently changed the time resolution of every run whose requested
        # count was not a divisor of the slip-node grid; a consumer can now say
        # "requested 40 -> ran 36" instead of presenting 36 as if it were asked
        # for.  ``slip_nodes_per_period`` is the grid that decides the snap.
        "n_steps_per_period_requested": int(_req_steps),
        "steps_snapped": bool(int(n_steps_per_period) != int(_req_steps)),
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
        "T_em_Nm": _T2, "T_avg_Nm": Tavg, "T_ripple_pct": Trip_raw,
        "T_ripple_raw_pct": Trip_raw, "T_ripple_filt_pct": Trip_raw,
        # Deprecated aliases retain raw values; no filtering/noise estimate.
        "T_noise_floor_pct": None, "torque_filter_applied": False,
        "T_em_raw_Nm": list(_T2), "T_em_filt_Nm": _T_report,
        "torque_method": _torque_method,
        "T_avg_maxwell_Nm": T_maxwell_avg,
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp,
        "T_em_maxwell_Nm": list(_T2raw),
        "psi_A_Wb": _psiA, "psi_B_Wb": _psiB, "psi_C_Wb": _psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
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
        "eddy_settled": bool(_warm_quiet is not False),
        "eddy_capped": bool(_warm_quiet is False),
        # The same two numbers as eddy_warmup_resid / eddy_warmup_tol, under
        # the names the settle test itself uses — these are the pair the
        # sweep points and the UI carry (the eddy_warmup_* names stay for the
        # physics-regression pins and the existing Simulation payload).
        "eddy_settle_residual": (None if (_warm_resid is None
                                          or not math.isfinite(_warm_resid))
                                 else float("%.3g" % _warm_resid)),
        "eddy_settle_tol": float(_EDDY_SETTLE_TOL),
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
        # …and the period-mean DC anchor's applications (B5).
        "v_dc_anchor_applied": int(_dc_anchor_applied),
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
    drive: str = "current",          # "current" | "voltage" | "pwm_voltage" | "custom_current"
    v_phase_peak: float = 0.0,
    v_delta_deg: float = 0.0,
    v_bus: float = 0.0,              # pwm_voltage: DC link [V]
    f_switch: float = 0.0,           # pwm_voltage: carrier [Hz] (snapped, see simulation/pwm.py)
    waveform=None,                   # custom_current: [(θ_e_deg, i_A)] over one electrical period
    i_block: float = 0.0,            # bldc_current: flat-top block amplitude [A terminal]
    excitation=None,                 # an ExcitationSource OBJECT instead of the five named
                                     # drives — an external controller / co-simulation
                                     # (simulation/excitation.py).  None = build it from
                                     # `drive` and the keywords above, as before.
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
        drive=str(drive or "current"), v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg),
        v_bus=float(v_bus), f_switch=float(f_switch), waveform=waveform,
        i_block=float(i_block),
        excitation=excitation,
        element_order=int(element_order),
        return_frames=int(return_frames),
        eddy=bool(eddy),
        # field_first is NOT set: the snapshot is the LAST frame, the one whose
        # B(t) history is complete, which is what the loss map and the coupled
        # eddy J are derived from.
        return_field=bool(return_field))
