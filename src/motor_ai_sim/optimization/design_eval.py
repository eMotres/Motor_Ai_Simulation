"""In-memory analytical evaluation of one motor design.

A *design* = a geometry dict (subset of config["geometry"], overlaid on the
baseline) + an operating point (gamma, current, rpm).  ``evaluate_design``
returns the performance metrics used as optimization objectives — torque,
torque density, efficiency, losses, masses — in well under a millisecond, so a
Pareto search can evaluate thousands of candidates in a couple of seconds.

PHYSICS
-------
* Torque from a magnetic-circuit air-gap flux density
      B_g = Br · h_mag / (h_mag + mu_rec · k_c · g)
  so torque responds to magnet height and air gap, then a flux-linkage torque
      T = (3/2) · p · psi_pm · I_q,   psi_pm = k_w · N · (2/pi) · B_g1 · tau_p · L
* Iron loss from the ASSIGNED steel's own Bertotti coefficients (the same
  kh/kc/ke the FEM solver uses), not a power law fitted to some other lamination.
* Copper loss and the masses replicate the validated formulas used by the
  Simulation tab (``masses.compute_masses``, ``fem_solver_2d.copper_loss_W``), so
  the optimizer and the simulator agree by construction — no factor needed.
* Imports only math/numpy at module level.  The material library, the winding
  layout and the mass helper are imported lazily inside functions, so this module
  still cannot pull in the FEM stack or perturb Simulation.

WHERE THE NUMBERS COME FROM  (read before trusting an ABSOLUTE value)
---------------------------------------------------------------------
Nothing machine-specific is a literal any more.  Everything is either read from
the design or derived from a pinned FEM result:

* ``k_w``   — the fundamental winding factor, computed from the ACTUAL winding
  layout by the star of slots (``_winding_factor``).  It used to be the literal
  0.933, which is the DOUBLE-layer value for a 12s14p/24s28p tooth-coil machine;
  the active design's config says ``winding.layers = 1``, whose winding factor is
  0.966.  A 3.5 % error straight into psi_pm and therefore into every torque
  this surrogate has ever reported.
* ``Br``, ``mu_rec``, ``sigma_mag`` — from the magnet material actually assigned
  to the design (request override first, then the config assignment), exactly as
  the FEM solver resolves it.  The literal was Br = 1.23 T; the assigned
  F45SH_120C is 1.19 T.
* ``kh/kc/ke``, steel density — from the assigned stator steel.  The literal was
  K_ST = 0.001088, a f^1.6 power law fitted to 20SW1200; the active machine is
  B15AHV950M.
* The four calibration scalars (torque, iron, magnet eddy, cogging) are DERIVED
  at first use from the pinned regression cases in
  ``tests/physics_baseline.json`` — see ``_ANCHOR_*`` and ``_derive_calibration``.
  They are no longer literals inherited from a retired 24s28p 24.87 N·m machine.

HONESTY ABOUT THE CALIBRATION
-----------------------------
A single scalar per quantity, fitted at ONE operating point of ONE machine, is
an anchor — not a validation.  It makes the surrogate's absolute value right
THERE and says nothing about anywhere else.  So the surrogate reports its own
measured uncertainty alongside every number it returns:

* ``cal_anchor``              — the machine + operating point the scalars are
                                pinned to, in one line.
* ``T_em_uncertainty_pct``    — the MEASURED spread of the surrogate's torque
                                against full P2 FEM runs of perturbed geometries
                                around the anchor.  Currently **9.4 %** (p90 over
                                8 points; see ``_UNCERTAINTY`` for the table).
* ``T_ripple_uncertainty_pct``— same for the ripple estimate: **78 %**.  The
                                surrogate's ripple is a slot-opening/air-gap
                                cogging index and cannot see load harmonics,
                                saturation or the sector sub-harmonic.

Who actually reads this module, measured rather than assumed: only
``optimization.optimizer.run_pareto_search`` (the ``POST /api/optimization/run``
Pareto search) and the ``motor_ai_sim.optimization`` package export.  The DoE
(``optimization.doe``) and the sweep/descent go straight to the FEM through
``routes.optimization._subprocess_eval`` — they never touch these scalars.  That
is the whole blast radius.

A consumer RANKING designs is using relative trends, which is what this model is
for — but note the measured blind spots in ``_UNCERTAINTY`` before ranking on a
knob the surrogate cannot feel (tooth width is the sharp one: it moves the FEM
torque and not the surrogate's at all).  Any consumer quoting an ABSOLUTE number
must quote the uncertainty with it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional, Tuple

# ── Material constants that are genuinely universal ──────────────────────────
RHO_CU_20 = 1.724e-8      # copper resistivity @20 °C [Ω·m]
ALPHA_CU  = 0.00393       # copper temp coefficient [1/K]

# ── Fallbacks, used ONLY when the assigned material cannot be resolved ───────
# (a headless call with no config, a material name the library does not know).
# Every one of them is reported through DesignMetrics.material_source so a number
# produced on a fallback is never mistaken for one produced from the design.
BR_FALLBACK      = 1.19       # T   — F45SH at 120 °C, the project's magnet
MU_REC_FALLBACK  = 1.05       # –   — NdFeB recoil permeability
SIGMA_MAG_FALLBACK = 6.25e5   # S/m — NdFeB
SIGMA_SHAFT_DEFAULT = 2.5e7   # S/m — Al6061 (kept: only _P carries it)

CARTER_K  = 1.05          # Carter factor proxy (slot-opening air-gap lengthening)
B_SAT     = 2.2           # tooth/back flux density above which a design is rejected

# ─────────────────────────────────────────────────────────────────────────────
# Calibration anchor
# ─────────────────────────────────────────────────────────────────────────────
# The four scalars below are fitted so the surrogate reproduces the FEM at ONE
# point.  That point is the p2_load / p2_eddy pair of tests/physics_baseline.json
# — the 30 mm 12s14p spoke machine that is the active design — so the anchor
# moves with the project instead of being frozen at a retired machine.
#
# Reproduce the anchor exactly with:
#     python -m pytest tests/test_physics_regression.py -q       # pins still green
#     python -c "from motor_ai_sim.optimization.design_eval import _calibration; \
#                print(_calibration())"
#
# If a pin is regenerated, the scalars follow automatically — there is nothing to
# edit here.  That is the whole point of deriving instead of hard-coding.
_ANCHOR_GEO: Dict[str, float] = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}
# The winding / operating point the pins were produced at.  n_series / n_parallel
# and rpm come from config at solve time in the regression suite; these are the
# values the current baseline was generated with.
_ANCHOR_WIND: Dict[str, Any] = {"n_series": 2, "n_parallel": 1, "layers": 1}
_ANCHOR_OP: Dict[str, float] = {"gamma_deg": 0.0, "current_a": 60.0,
                                "rpm": 15000.0, "coil_temp_c": 120.0}
_ANCHOR_MAGNET = "F45SH_120C"     # tests/test_physics_regression.py MAGNET
_ANCHOR_STEEL  = "B15AHV950M"     # config assignment at baseline generation
# The pinned FEM values themselves (tests/physics_baseline.json).  Read from the
# file when it is present so a regenerated pin propagates; these literals are the
# fallback for an installed package with no tests/ directory.
_ANCHOR_FEM: Dict[str, float] = {
    "T_avg_Nm":     0.41740098476923165,   # p2_load
    "P_fe_W":       3.1225884334434255,    # p2_load
    "P_mag_W":      1.382,                 # p2_eddy P_mag_solve_W (rotor_eddy on)
    "T_ripple_pct": 0.5348607680815186,    # p2_load
}

# Measured uncertainty of the anchored surrogate.  Not a guess and not a zero:
# ``scripts/_surrogate_uncertainty.py`` runs the FULL p2_load P2 transient on
# geometries perturbed one knob at a time around the anchor and compares.
# Measured 2026-07-29, 8 points (surrogate vs FEM, error = (sur−FEM)/FEM):
#
#     tooth_width      2.2   FEM 0.4023  sur 0.4174   +3.8 %   ripple  −78 %
#     tooth_width      3.0   FEM 0.4111  sur 0.4174   +1.5 %   ripple   −7 %
#     magnet_height    3.8   FEM 0.3985  sur 0.4149   +4.1 %   ripple  −24 %
#     magnet_height    5.2   FEM 0.4268  sur 0.4194   −1.8 %   ripple  −57 %
#     slot_height      3.8   FEM 0.4461  sur 0.4403   −1.3 %   ripple  −72 %
#     air_gap          0.3   FEM 0.3721  sur 0.4070   +9.4 %   ripple  +65 %
#     magnet_fill_down 0.78  FEM 0.4103  sur 0.3976   −3.1 %   ripple  +51 %
#     num_wires        5     FEM 0.3563  sur 0.3478   −2.4 %   ripple  −54 %
#
# Two specific blind spots the table makes visible, worth more than the summary
# number:
#   * TOOTH WIDTH does not move the surrogate's torque AT ALL (0.4174 at both
#     2.2 and 3.0 mm) — B_g comes from the magnet circuit, which has no tooth in
#     it — while the FEM moves 2.2 %.  A tooth-width sweep ranked on this
#     surrogate is ranking on mass and loss only.
#   * AIR GAP is the worst single knob (+9.4 %): the Carter proxy k_c = 1.05 is
#     a constant, so widening the gap costs the surrogate less flux than it
#     costs the real machine.
#
# The ripple column is why the ripple estimate carries its own (large) number:
# the surrogate's ripple is a slot-opening/air-gap cogging index and cannot see
# load harmonics, saturation or the sector sub-harmonic.
_UNCERTAINTY: Dict[str, float] = {
    # p90 of |surrogate − FEM| / FEM over the perturbation set, in percent.
    "T_em_pct": 9.4,         # -1 would mean "not measured"
    "T_ripple_pct": 77.6,
    "n_points": 8,
}


@dataclass
class _P:
    """Minimal in-memory params (radii + topology + slot/wire geometry).

    Replicates geometry_2d.params_from_config WITHOUT reading the YAML file, so
    candidate designs never touch global state.
    """
    r_stator_out: float
    r_stator_in:  float
    r_rotor_out:  float
    r_rotor_in:   float
    r_shaft_in:   float
    r_air_out:    float
    r_air_in:     float
    num_poles:    int
    num_slots:    int
    stack_length: float
    magnet_fill_fraction: float
    slot_width_m:  float
    slot_height_m: float
    wire_width_m:  float
    wire_height_m: float
    num_wires_per_slot: int
    sigma_mag:   float = SIGMA_MAG_FALLBACK
    sigma_shaft: float = SIGMA_SHAFT_DEFAULT


def build_params(geo: Dict[str, Any]) -> _P:
    """Build in-memory params from a geometry dict (mirror of
    geometry_2d.params_from_config radii math)."""
    mm = 1e-3
    r_so = geo["stator_diameter"] / 2 * mm
    r_si = r_so - geo["core_thickness"] * mm - geo["slot_height"] * mm
    r_ro = r_si - geo["air_gap"] * mm
    r_ri = r_ro - geo["magnet_height"] * mm - geo["rotor_house_height"] * mm
    r_sh = r_ri - geo["shaft_height"] * mm
    num_slots = int(geo.get("num_slots") or round(geo["num_seg"] * geo["num_slots_per_segment"]))
    num_poles = int(geo.get("num_poles") or round(geo["num_seg"] * geo["num_poles_per_segment"]))
    slot_width_m = (geo["wire_width"] + 2 * geo["wire_spacing_x"]
                    + 2 * geo["insulation_thickness"]) * mm
    return _P(
        r_stator_out=r_so, r_stator_in=r_si, r_rotor_out=r_ro, r_rotor_in=r_ri,
        r_shaft_in=r_sh, r_air_out=r_si, r_air_in=r_ro,
        num_poles=num_poles, num_slots=num_slots,
        stack_length=geo.get("motor_length", 30) * mm,
        magnet_fill_fraction=geo.get("magnet_fill_down", 0.9),
        slot_width_m=slot_width_m, slot_height_m=geo["slot_height"] * mm,
        wire_width_m=geo["wire_width"] * mm, wire_height_m=geo["wire_height"] * mm,
        num_wires_per_slot=int(geo["num_wires_per_slot"]),
    )


def geometry_is_valid(p: _P) -> bool:
    """A candidate is physically valid iff the radii stay strictly ordered and
    positive — guards the search from ever producing a degenerate geometry."""
    return (p.r_stator_out > p.r_stator_in > p.r_rotor_out > p.r_rotor_in
            > p.r_shaft_in > 0.0
            and p.slot_width_m > 0 and p.slot_height_m > 0
            and p.wire_width_m > 0 and p.wire_height_m > 0)


# ─────────────────────────────────────────────────────────────────────────────
# Topology and materials — read from the design, never assumed
# ─────────────────────────────────────────────────────────────────────────────
_KW_CACHE: Dict[Tuple[int, int, bool], float] = {}


def _winding_factor(num_slots: int, num_poles: int, single_layer: bool = True) -> float:
    """Fundamental winding factor k_w1 from the ACTUAL winding layout.

    Star of slots: with slot i carrying phase-A conductors of sign d_i at
    mechanical angle theta_i = 2*pi*i/Q, the phase-A fundamental EMF phasor is
    sum(d_i * exp(j*p*theta_i)), and k_w1 is its magnitude divided by the number
    of phase-A slot sides (the value that sum would reach if every side were in
    phase — a full-pitch, undistributed winding).

    This is derived, not looked up, so it is right for whatever slot/pole count
    and layer count the candidate has.  Cross-checked against published
    fractional-slot concentrated-winding tables:

        12s8p                  -> 0.866   (published 0.866)
        12s10p single-layer    -> 0.966   (published 0.966)
        12s14p single-layer    -> 0.966   (published 0.966)
        24s28p single-layer    -> 0.966   (same unit machine, x2)

    The retired literal K_W = 0.933 is the published DOUBLE-layer number for this
    family.  The active design is single-layer — config carries
    ``winding.layers = 1`` and the UI draws A a c C B b a A C c b B, which is
    exactly what ``build_winding_layout(12, 7, single_layer=True)`` returns — so
    0.933 understated psi_pm, and therefore every surrogate torque, by 3.5 %.

    One caveat worth stating rather than hiding: ``build_winding_layout``'s
    ``single_layer=False`` branch emits ONE phasor per slot (a rotated version of
    the same pattern), not two coil sides per slot, so this function returns
    0.966 for both of its modes on 12s14p — it cannot currently reproduce the
    0.933 double-layer case.  For the active machine that is moot (it IS
    single-layer), and the number is derived from whatever layout the solver
    would actually energise, which is the property that matters.
    """
    key = (int(num_slots), int(num_poles), bool(single_layer))
    hit = _KW_CACHE.get(key)
    if hit is not None:
        return hit
    kw = 0.0
    try:
        import cmath
        from motor_ai_sim.simulation.geometry_2d import build_winding_layout
        p_pairs = max(int(num_poles) // 2, 1)
        layout = build_winding_layout(int(num_slots), p_pairs,
                                      single_layer=bool(single_layer))
        acc, n = 0j, 0
        for i, (phase, direction) in enumerate(layout):
            if phase != "A":
                continue
            acc += direction * cmath.exp(1j * p_pairs * 2.0 * math.pi * i / num_slots)
            n += 1
        if n:
            kw = abs(acc) / n
    except Exception:  # noqa: BLE001 — a topology we cannot lay out is not fatal
        kw = 0.0
    # A layout we could not build (odd slot count in single-layer, an unusable
    # slot/pole pair) gives 0; fall back to the ideal 1.0 rather than silently
    # zeroing every torque, and let the saturation/feasibility gates judge it.
    kw = kw if kw > 1e-3 else 1.0
    _KW_CACHE[key] = kw
    return kw


def _magnet_props(name: Optional[str] = None) -> Tuple[float, float, float, str]:
    """(Br [T], mu_rec, sigma [S/m], source) of the magnet this design uses.

    Resolution order is the SAME the FEM solver uses (fem_solver_2d): the
    per-request material override first (multi-user), then the config
    assignment, then the library.  ``name`` forces a specific magnet — used by
    the calibration, which must reproduce the pinned run's magnet exactly.
    """
    mag = name
    if mag is None:
        try:
            from motor_ai_sim.material_context import get_request_materials
            ov = get_request_materials() or {}
            mag = ((ov.get("assignment") or {}).get("magnet")) or None
        except Exception:  # noqa: BLE001
            mag = None
    if mag is None:
        try:
            from motor_ai_sim.config import get_config
            mag = (get_config().get("materials") or {}).get("magnet") or None
        except Exception:  # noqa: BLE001
            mag = None
    if mag:
        try:
            from motor_ai_sim.materials import get_magnet
            m = get_magnet(str(mag))
            return (float(m.Br) or BR_FALLBACK,
                    float(m.mu_rec) or MU_REC_FALLBACK,
                    float(m.sigma) or SIGMA_MAG_FALLBACK,
                    str(mag))
        except Exception:  # noqa: BLE001
            pass
    return BR_FALLBACK, MU_REC_FALLBACK, SIGMA_MAG_FALLBACK, "fallback(no magnet assigned)"


def _steel_props(name: Optional[str] = None) -> Tuple[float, float, float, str]:
    """(kh, kc, ke, source) Bertotti coefficients [W/m³ basis] of the stator steel.

    Same resolution order as the magnet, and the same coefficients the FEM solver
    bills iron loss with (``materials.effective_bertotti`` — fitted from the
    measured loss curves when the datasheet has them).  Returning zeros means the
    steel has no loss data at all, which the caller reports rather than papering
    over with a power law borrowed from a different lamination.
    """
    st = name
    if st is None:
        try:
            from motor_ai_sim.material_context import get_request_materials
            ov = get_request_materials() or {}
            st = ((ov.get("assignment") or {}).get("stator_core")) or None
        except Exception:  # noqa: BLE001
            st = None
    if st is None:
        try:
            from motor_ai_sim.config import get_config
            st = (get_config().get("materials") or {}).get("stator_core") or None
        except Exception:  # noqa: BLE001
            st = None
    if st:
        try:
            from motor_ai_sim.materials import get_steel, effective_bertotti
            kh, kc, ke = effective_bertotti(get_steel(str(st)))
            if (kh or kc or ke):
                return float(kh), float(kc), float(ke), str(st)
        except Exception:  # noqa: BLE001
            pass
    return 0.0, 0.0, 0.0, "fallback(no steel loss data)"


def _end_winding_factor(p: _P, geo: Dict[str, Any]) -> float:
    """k_end end-winding length factor — delegates to the SINGLE SOURCE
    ``motor_ai_sim.masses.end_winding_factor`` so the optimizer's analytic copper
    loss uses the EXACT same k_end as the mass (compute_masses) and the FEM solver
    (copper_loss_W).  (The old local copy used ``tau − slot_width`` and so was frozen
    under a tooth-width sweep, while the mass tracked the tooth — they disagreed.)"""
    from motor_ai_sim.masses import end_winding_factor
    return end_winding_factor(p, geo)


def _copper_loss(p: _P, geo: Dict[str, Any], I_phase_rms: float,
                 n_parallel: float, coil_temp_c: float) -> float:
    """ρ_Cu(T)·J²·V_cu·k_end (replicates fem_solver_2d.copper_loss_W)."""
    n_wires = float(geo.get("num_wires_per_slot", 14))
    wire_area = p.wire_width_m * p.wire_height_m
    n_par = max(float(n_parallel), 1.0)
    if wire_area <= 0 or I_phase_rms <= 0:
        return 0.0
    V_cu_slot = p.num_slots * wire_area * n_wires * p.stack_length
    k_end = _end_winding_factor(p, geo)
    rho = RHO_CU_20 * (1.0 + ALPHA_CU * (coil_temp_c - 20.0))
    I_coil = I_phase_rms / n_par
    J = I_coil / wire_area
    return rho * J * J * V_cu_slot * k_end


def _masses(p: _P, geo: Dict[str, Any]) -> Dict[str, float]:
    """Component masses — delegates to the SINGLE SOURCE
    ``motor_ai_sim.masses.compute_masses`` (stator iron = back-iron yoke + teeth∝
    tooth_width), so the sweep / all three optimizers use the exact same mass — and
    therefore torque-density — as Simulation.  (The old local copy parametrised the
    stator iron by slot_width only, so a tooth-width sweep left the mass constant.)"""
    from motor_ai_sim.masses import compute_masses
    return compute_masses(p, geo)


# ─────────────────────────────────────────────────────────────────────────────
# Raw (UNCALIBRATED) physics — the calibration divides the FEM pin by these
# ─────────────────────────────────────────────────────────────────────────────
def _raw_physics(geo: Dict[str, Any], wind: Dict[str, Any], sim: Dict[str, Any],
                 gamma_deg: float, current_a: float, rpm: float,
                 coil_temp_c: float,
                 magnet: Optional[str] = None,
                 steel: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Every quantity the surrogate models, BEFORE any calibration scalar.

    Returns None for a geometry the radii reject.  Split out from
    ``evaluate_design`` so ``_derive_calibration`` can fit the scalars against
    exactly the same physics the caller will get.
    """
    p = build_params(geo)
    if not geometry_is_valid(p):
        return None

    Br, mu_rec, sigma_mag, mag_src = _magnet_props(magnet)
    kh, kc, ke, steel_src = _steel_props(steel)

    pole_pairs = max(p.num_poles // 2, 1)
    n_series = float(wind.get("n_series", 2))
    n_parallel = float(wind.get("n_parallel", 2))
    single_layer = int(wind.get("layers", 1) or 1) <= 1
    k_w = _winding_factor(p.num_slots, p.num_poles, single_layer)
    n_wires = float(geo.get("num_wires_per_slot", 14))
    I_rms = float(current_a)
    freq = rpm / 60.0 * pole_pairs
    omega_mech = 2 * math.pi * rpm / 60.0

    # ── Air-gap flux density via the magnet circuit ───────────────────────────
    h_mag = p.r_rotor_out - p.r_rotor_in          # radial magnet height [m]
    g = p.r_air_out - p.r_air_in                  # air gap [m]
    if h_mag <= 0:
        return None
    B_g = Br * h_mag / (h_mag + mu_rec * CARTER_K * g)
    alpha = min(max(p.magnet_fill_fraction, 0.05), 1.0)   # pole-arc coverage
    B_g1 = B_g * math.sin(alpha * math.pi / 2.0)          # fundamental [T]

    # ── Flux linkage & torque ─────────────────────────────────────────────────
    r_ag = (p.r_air_out + p.r_air_in) / 2.0
    tau_p = 2 * math.pi * r_ag / p.num_poles
    N_branch = n_series * n_wires
    psi_pm = k_w * N_branch * (2 / math.pi) * B_g1 * tau_p * p.stack_length
    I_q = I_rms * math.sqrt(2) * math.cos(math.radians(gamma_deg))
    T_em_raw = 1.5 * pole_pairs * psi_pm * I_q

    # ── Iron loss: the assigned steel's own Bertotti, on the modelled B ───────
    N_slots = p.num_slots
    slot_pitch = math.pi * 2 * p.r_stator_in / N_slots
    tooth_w = float(geo.get("tooth_width", 9.2)) * 1e-3
    core_thick = float(geo.get("core_thickness", 4.2)) * 1e-3
    B_tooth_raw = B_g * slot_pitch / max(tooth_w, 1e-4)
    B_back_raw = B_tooth_raw * tooth_w / max(2.0 * core_thick, 1e-4)
    B_tooth = min(2.0, B_tooth_raw)          # clamped for the LOSS only
    B_back = min(2.0, B_back_raw)
    B_stat_eff = math.sqrt((B_tooth ** 2 + B_back ** 2) / 2.0)
    masses = _masses(p, geo)
    f_slot = freq * N_slots / pole_pairs
    B_rotor = min(1.4, B_g * 0.6)

    def _bertotti_w_per_kg(f: float, B: float, rho: float) -> float:
        if rho <= 0:
            return 0.0
        return (kh * f * B ** 2 + kc * f ** 2 * B ** 2
                + ke * f ** 1.5 * B ** 1.5) / rho

    rho_fe = 7650.0
    try:                                     # the steel's own density if known
        from motor_ai_sim.materials import get_steel
        if steel_src and not steel_src.startswith("fallback"):
            rho_fe = float(get_steel(steel_src).density) or rho_fe
    except Exception:  # noqa: BLE001
        pass
    P_fe_raw = (_bertotti_w_per_kg(freq, B_stat_eff, rho_fe) * masses["stator"]
                + _bertotti_w_per_kg(f_slot, B_rotor, rho_fe) * masses["rotor"])

    # ── Magnet eddy (slot-harmonic), classical eddy-in-slab ───────────────────
    # P = sigma * omega_slot^2 * B_ac^2 * d^2 * V / 24, driven by slot-passing
    # frequency, the slot-harmonic air-gap flux and the magnet volume.
    omega_slot = 2 * math.pi * f_slot
    B_mag_ac = min(B_g * (1 - tooth_w / slot_pitch) * 0.4, 0.25)   # slot harmonic [T]
    r_mid_mag = (p.r_rotor_out + p.r_rotor_in) / 2
    d_tang = 2 * math.pi * r_mid_mag / p.num_poles * p.magnet_fill_fraction
    d_eff = min(d_tang, h_mag)
    V_mag = (math.pi * (p.r_rotor_out ** 2 - p.r_rotor_in ** 2)
             * p.stack_length * p.magnet_fill_fraction)
    P_mag_raw = sigma_mag * omega_slot ** 2 * B_mag_ac ** 2 * d_eff ** 2 * V_mag / 24.0

    # ── Copper loss — no calibration, same formula as the solver ──────────────
    P_cu = _copper_loss(p, geo, I_rms, n_parallel, coil_temp_c)

    # ── Cogging / slot-ripple INDEX (dimensionless, calibrated below) ─────────
    slot_opening = max(slot_pitch - tooth_w, 0.5e-3)
    cog_index = (slot_opening / max(g, 1e-4)) * (1.0 + 0.5 * abs(p.magnet_fill_fraction - 0.85))

    return {
        "p": p, "masses": masses,
        "T_em_raw": T_em_raw, "P_fe_raw": P_fe_raw, "P_mag_raw": P_mag_raw,
        "P_cu": P_cu, "cog_index": cog_index,
        "P_mech_per_T": omega_mech,
        "B_g": B_g, "B_tooth_raw": B_tooth_raw, "B_back_raw": B_back_raw,
        "slot_pitch": slot_pitch, "tooth_w": tooth_w,
        "k_w": k_w, "Br": Br, "magnet_source": mag_src, "steel_source": steel_src,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Calibration, derived from the pinned FEM cases (never a literal)
# ─────────────────────────────────────────────────────────────────────────────
_CAL: Optional[Dict[str, float]] = None


def _anchor_pins() -> Dict[str, float]:
    """The pinned FEM numbers, read from tests/physics_baseline.json when it is
    there (so regenerating a pin re-anchors the surrogate automatically), else
    the literals recorded in ``_ANCHOR_FEM``."""
    pins = dict(_ANCHOR_FEM)
    try:
        import json
        from pathlib import Path
        f = Path(__file__).resolve().parents[3] / "tests" / "physics_baseline.json"
        b = json.loads(f.read_text(encoding="utf-8"))
        load = b.get("p2_load") or {}
        eddy = b.get("p2_eddy") or {}
        if load.get("T_avg_Nm"):
            pins["T_avg_Nm"] = float(load["T_avg_Nm"])
        if load.get("P_fe_W"):
            pins["P_fe_W"] = float(load["P_fe_W"])
        if load.get("T_ripple_pct"):
            pins["T_ripple_pct"] = float(load["T_ripple_pct"])
        if eddy.get("P_mag_solve_W"):
            pins["P_mag_W"] = float(eddy["P_mag_solve_W"])
    except Exception:  # noqa: BLE001 — installed package with no tests/
        pass
    return pins


def _derive_calibration() -> Dict[str, float]:
    """Fit one scalar per quantity so the surrogate reproduces the pinned FEM at
    the anchor point.  k = FEM_pin / surrogate_raw — nothing else.

    What each scalar is correcting, honestly:

    * ``torque`` — the magnetic-circuit B_g ignores iron reluctance, slot
      leakage and saturation; the flux-linkage torque ignores reluctance torque.
      The scalar absorbs all of it AT THE ANCHOR.
    * ``iron`` — the Bertotti coefficients are right (they are the solver's own),
      but B_stat_eff is a two-point estimate of a field the FEM resolves
      element-by-element, and the rotor term is a guess (0.6 * B_g).
    * ``magnet`` — the eddy-in-slab form has no reaction field and no 2-D
      end effect; the FEM pin comes from the coupled sigma*dA/dt solve.
    * ``cogging`` — the index is geometry only.  Anchoring it on the p2_load
      LOADED ripple means the surrogate's "ripple" is really "the loaded ripple
      a machine like the anchor has, scaled by slot-opening/air-gap".  It cannot
      see load harmonics, saturation or the sector sub-harmonic, which is why
      its measured uncertainty is reported and is large.
    """
    pins = _anchor_pins()
    raw = _raw_physics(dict(_ANCHOR_GEO), dict(_ANCHOR_WIND), {},
                       _ANCHOR_OP["gamma_deg"], _ANCHOR_OP["current_a"],
                       _ANCHOR_OP["rpm"], _ANCHOR_OP["coil_temp_c"],
                       magnet=_ANCHOR_MAGNET, steel=_ANCHOR_STEEL)
    if not raw:
        # The anchor geometry itself is invalid — impossible unless this file was
        # edited wrongly.  Refuse to invent scalars.
        return {"torque": 1.0, "iron": 1.0, "magnet": 1.0, "cog": 0.0,
                "anchored": 0.0}

    def _k(pin: float, raw_v: float) -> float:
        return float(pin) / float(raw_v) if abs(raw_v) > 1e-12 else 1.0

    k_t = _k(pins["T_avg_Nm"], raw["T_em_raw"])
    k_fe = _k(pins["P_fe_W"], raw["P_fe_raw"])
    k_mag = _k(pins["P_mag_W"], raw["P_mag_raw"])
    # Cogging: the pin is a ripple PERCENTAGE of the anchor's torque, so convert
    # it to the absolute cogging torque the index has to reproduce.
    T_cog_pin = pins["T_ripple_pct"] / 100.0 * abs(pins["T_avg_Nm"])
    k_cog = _k(T_cog_pin, raw["cog_index"])
    # If the anchor's own magnet or steel could not be resolved (a stripped
    # material library), the scalars were fitted against fallback physics — a
    # different thing from what the pin was produced with.  Say so rather than
    # let it pass as an anchored calibration.
    ok_mat = float(not (raw["magnet_source"].startswith("fallback")
                        or raw["steel_source"].startswith("fallback")))
    return {"torque": k_t, "iron": k_fe, "magnet": k_mag, "cog": k_cog,
            "anchored": ok_mat,
            "raw_T": raw["T_em_raw"], "raw_fe": raw["P_fe_raw"],
            "raw_mag": raw["P_mag_raw"], "raw_cog_index": raw["cog_index"],
            "k_w_anchor": raw["k_w"], "Br_anchor": raw["Br"]}


def _calibration() -> Dict[str, float]:
    """Lazily derived, then cached.  Lazy because resolving the winding layout
    and the material library costs ~2 s of imports the first time, and importing
    this module must stay cheap."""
    global _CAL
    if _CAL is None:
        _CAL = _derive_calibration()
    return _CAL


def _anchor_line() -> str:
    """One line naming what the absolute numbers are pinned to."""
    warn = "" if _calibration().get("anchored", 0.0) else \
        "  [WARNING: anchor materials unresolved — scalars fitted on fallbacks]"
    return (f"30 mm 12s14p (tests p2_load/p2_eddy): "
            f"T={_anchor_pins()['T_avg_Nm']:.4f} N·m @ "
            f"{_ANCHOR_OP['current_a']:.0f} A_rms, gamma={_ANCHOR_OP['gamma_deg']:.0f}°, "
            f"{_ANCHOR_OP['rpm']:.0f} rpm, {_ANCHOR_MAGNET}" + warn)


@dataclass
class DesignMetrics:
    feasible: bool
    reason: str = ""
    # objectives
    T_em_Nm: float = 0.0
    efficiency: float = 0.0
    torque_per_mass_Nm_kg: float = 0.0
    power_per_mass_W_kg: float = 0.0
    # supporting
    P_mech_W: float = 0.0
    P_loss_total_W: float = 0.0
    P_cu_W: float = 0.0
    P_fe_W: float = 0.0
    P_mag_W: float = 0.0
    mass_total_kg: float = 0.0
    B_gap_T: float = 0.0
    B_tooth_T: float = 0.0
    B_back_T: float = 0.0
    T_cog_Nm: float = 0.0
    T_ripple_pct: float = 0.0
    current_a: float = 0.0
    rpm: float = 0.0
    # ── provenance / uncertainty ─────────────────────────────────────────────
    # What the absolute numbers are anchored to, what the model read from the
    # design, and how far off it was measured to be.  A consumer quoting an
    # absolute value has to quote these with it.
    k_w: float = 0.0                     # winding factor actually used
    Br_T: float = 0.0                    # magnet remanence actually used
    material_source: str = ""            # "<magnet> / <steel>"
    cal_anchor: str = ""
    T_em_uncertainty_pct: float = -1.0       # -1 = not measured
    T_ripple_uncertainty_pct: float = -1.0   # -1 = not measured
    overrides: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {k: (round(v, 5) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


def evaluate_design(geo: Dict[str, Any], wind: Dict[str, Any], sim: Dict[str, Any],
                    gamma_deg: float, current_a: float, rpm: float,
                    coil_temp_c: float = 120.0,
                    overrides: Dict[str, float] | None = None) -> DesignMetrics:
    """Evaluate one design. ``geo`` is the FULL geometry dict (baseline already
    overlaid with the candidate's overrides). Pure in-memory, ~0.1 ms after the
    first call (which derives the calibration)."""
    raw = _raw_physics(geo, wind, sim, gamma_deg, current_a, rpm, coil_temp_c)
    if raw is None:
        return DesignMetrics(feasible=False, reason="invalid geometry (radii)",
                             overrides=overrides or {})

    cal = _calibration()
    p = raw["p"]
    masses = raw["masses"]

    T_em = cal["torque"] * raw["T_em_raw"]
    P_mech = T_em * raw["P_mech_per_T"]
    P_fe = cal["iron"] * raw["P_fe_raw"]
    P_mag = cal["magnet"] * raw["P_mag_raw"]
    P_cu = raw["P_cu"]
    P_loss = P_cu + P_fe + P_mag
    eff = P_mech / (P_mech + P_loss) if P_mech > 0 else 0.0
    m_tot = masses["total"]

    # Ripple % = cogging / rated torque, so it FALLS as current (torque) rises —
    # that is why a design makes TWO different ripple points at I1 vs I2.
    T_cog = cal["cog"] * raw["cog_index"]
    ripple_pct = (100.0 * T_cog / abs(T_em)) if abs(T_em) > 1e-6 else 999.0

    # saturation feasibility (heavily saturated tooth/back = unrealizable design)
    B_tooth_raw, B_back_raw = raw["B_tooth_raw"], raw["B_back_raw"]
    sat_ok = B_tooth_raw < B_SAT and B_back_raw < B_SAT
    # buildability: the tooth must leave a real slot opening within the slot
    # pitch, else the CadQuery polygons overlap (TopologyException) and the FEM
    # refine can't mesh it.  Conservative: tooth ≤ 0.70 · slot pitch at the bore.
    tooth_fits = raw["tooth_w"] <= 0.70 * raw["slot_pitch"]
    feasible = sat_ok and tooth_fits
    if not sat_ok:
        reason = f"saturated (B_tooth={B_tooth_raw:.2f}, B_back={B_back_raw:.2f} > {B_SAT})"
    elif not tooth_fits:
        reason = (f"tooth too wide to build ({raw['tooth_w'] * 1e3:.1f} mm > "
                  f"{0.70 * raw['slot_pitch'] * 1e3:.1f} mm slot-pitch limit)")
    else:
        reason = ""

    return DesignMetrics(
        feasible=feasible, reason=reason,
        T_em_Nm=T_em, efficiency=eff,
        torque_per_mass_Nm_kg=(T_em / m_tot if m_tot > 0 else 0.0),
        power_per_mass_W_kg=(P_mech / m_tot if m_tot > 0 else 0.0),
        P_mech_W=P_mech, P_loss_total_W=P_loss,
        P_cu_W=P_cu, P_fe_W=P_fe, P_mag_W=P_mag,
        mass_total_kg=m_tot, B_gap_T=raw["B_g"],
        B_tooth_T=B_tooth_raw, B_back_T=B_back_raw,
        T_cog_Nm=T_cog, T_ripple_pct=ripple_pct,
        current_a=float(current_a), rpm=float(rpm),
        k_w=raw["k_w"], Br_T=raw["Br"],
        material_source=f"{raw['magnet_source']} / {raw['steel_source']}",
        cal_anchor=_anchor_line(),
        T_em_uncertainty_pct=_UNCERTAINTY["T_em_pct"],
        T_ripple_uncertainty_pct=_UNCERTAINTY["T_ripple_pct"],
        overrides=overrides or {},
    )
