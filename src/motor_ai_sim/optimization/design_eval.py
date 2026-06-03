"""In-memory analytical evaluation of one motor design.

A *design* = a geometry dict (subset of config["geometry"], overlaid on the
baseline) + an operating point (gamma, current, rpm).  ``evaluate_design``
returns the performance metrics used as optimization objectives — torque,
torque density, efficiency, losses, masses — in well under a millisecond, so a
Pareto search can evaluate thousands of candidates in a couple of seconds.

PHYSICS NOTES
-------------
* Torque uses a magnetic-circuit air-gap flux density
      B_g = Br_mag · h_mag / (h_mag + mu_r · k_c · g)
  so torque responds physically to magnet height (h_mag) and air gap (g) — the
  old ``_compute_torque`` used a constant Br at the air gap and was therefore
  blind to those two key design variables.  A single scalar ``K_TORQUE_CAL``
  aligns the surrogate's BASELINE torque with the validated sliding-band FEM
  (T ≈ 24.9 N·m); relative trends then come from the physics, not the fudge.
* Copper / iron / magnet losses replicate the validated analytical formulas
  used by the Simulation tab (copper_loss_W, _compute_losses) so the optimizer
  and the simulator agree at the baseline.
* This module imports ONLY math/numpy — it does NOT import the FEM stack or any
  routes module, so it is fast and cannot perturb Simulation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Dict, Any

# ── Material constants (match fem_solver_2d / _compute_masses) ────────────────
RHO_CU_20 = 1.724e-8      # copper resistivity @20 °C [Ω·m]
ALPHA_CU  = 0.00393       # copper temp coefficient [1/K]
RHO_STEEL = 7650.0        # silicon steel [kg/m³]
RHO_CU    = 8900.0        # copper [kg/m³]
RHO_MAG   = 7500.0        # NdFeB [kg/m³]
RHO_AL    = 2700.0        # Al6061 shaft [kg/m³]
SIGMA_MAG = 6.25e5        # NdFeB conductivity [S/m]

# ── Magnet / magnetic-circuit constants ──────────────────────────────────────
BR_MAG    = 1.23          # F45SH NdFeB remanence at operating temp [T]
#   → B_g(baseline) ≈ 1.19 T, matching the validated air-gap flux
MU_R_MAG  = 1.05          # NdFeB recoil relative permeability
CARTER_K  = 1.05          # Carter factor proxy (slot-opening air-gap lengthening)
K_ST      = 0.001088      # 20SW1200 specific-loss Steinmetz coeff (W/kg @50Hz,1T)
K_W       = 0.933         # winding factor, 24-slot/28-pole concentrated
B_SAT     = 2.2           # tooth/back flux density above which a design is rejected

# ── Surrogate→FEM calibration ────────────────────────────────────────────────
# A single scalar per quantity aligns the surrogate's BASELINE with the
# validated sliding-band FEM (T≈24.87 N·m, P_fe≈185 W, P_mag≈84 W).  Copper and
# mass already match exactly (same formulas), so they need no factor.  Relative
# trends come from the physics; these only fix the absolute anchor.
K_TORQUE_CAL = 1.1331     # baseline → 24.87 N·m (validated SB-FEM)
K_FE_CAL     = 0.5033     # baseline → 185 W iron
K_MAG_CAL    = 0.0657     # baseline → 84 W magnet eddy


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
    sigma_mag:   float = SIGMA_MAG
    sigma_shaft: float = 2.5e7


def build_params(geo: Dict[str, Any]) -> _P:
    """Build in-memory params from a geometry dict (mirror of
    geometry_2d.params_from_config radii math)."""
    mm = 1e-3
    r_so = geo["stator_diameter"] / 2 * mm
    r_si = r_so - geo["core_thickness"] * mm - geo["slot_height"] * mm
    r_ro = r_si - geo["air_gap"] * mm
    r_ri = r_ro - geo["magnet_height"] * mm - geo["rotor_house_height"] * mm
    r_sh = r_ri - geo["shaft_height"] * mm
    num_slots = int(geo["num_seg"] * geo["num_slots_per_segment"])
    num_poles = int(geo["num_seg"] * geo["num_poles_per_segment"])
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


def _end_winding_factor(p: _P, geo: Dict[str, Any]) -> float:
    """k_end = (π·tooth_w/2 + L)/L  — tooth-coil end-turn (validated formula)."""
    L = float(p.stack_length)
    if L <= 0:
        return 1.0
    r_mid = p.r_stator_in + p.slot_height_m * 0.5
    tau = 2.0 * math.pi * r_mid / max(p.num_slots, 1)
    tooth_w = max(tau - p.slot_width_m, 0.3 * tau)
    return (math.pi * tooth_w / 2.0 + L) / L


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
    """Component masses (replicates routes.simulation._compute_masses)."""
    L = p.stack_length
    n_wires = float(geo.get("num_wires_per_slot", 14))
    wire_area = p.wire_width_m * p.wire_height_m
    ns = p.num_slots

    V_stator_full = math.pi * (p.r_stator_out**2 - p.r_stator_in**2) * L
    V_slots = ns * p.slot_width_m * p.slot_height_m * L
    m_stator = max(V_stator_full - V_slots, 0.0) * RHO_STEEL

    r_mid_slot = p.r_stator_in + p.slot_height_m * 0.5
    tau_slot = 2 * math.pi * r_mid_slot / ns
    L_endturn = math.pi * tau_slot / 2 + p.slot_height_m
    V_cu = ns * wire_area * n_wires * (L + 2 * L_endturn)
    m_cu = V_cu * RHO_CU

    V_mag = math.pi * (p.r_rotor_out**2 - p.r_rotor_in**2) * L * p.magnet_fill_fraction
    m_mag = V_mag * RHO_MAG

    V_rotor = math.pi * (p.r_rotor_in**2 - p.r_shaft_in**2) * L
    m_rotor = max(V_rotor, 0.0) * RHO_STEEL

    V_shaft = math.pi * p.r_shaft_in**2 * L
    m_shaft = V_shaft * RHO_AL

    m_total = m_stator + m_cu + m_mag + m_rotor + m_shaft
    return {"stator": m_stator, "cu": m_cu, "mag": m_mag, "rotor": m_rotor,
            "shaft": m_shaft, "total": m_total}


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
    overrides: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {k: (round(v, 5) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


def evaluate_design(geo: Dict[str, Any], wind: Dict[str, Any], sim: Dict[str, Any],
                    gamma_deg: float, current_a: float, rpm: float,
                    coil_temp_c: float = 120.0,
                    overrides: Dict[str, float] | None = None) -> DesignMetrics:
    """Evaluate one design. ``geo`` is the FULL geometry dict (baseline already
    overlaid with the candidate's overrides). Pure in-memory, ~0.1 ms."""
    p = build_params(geo)
    if not geometry_is_valid(p):
        return DesignMetrics(feasible=False, reason="invalid geometry (radii)",
                             overrides=overrides or {})

    pole_pairs = max(p.num_poles // 2, 1)
    n_series = float(wind.get("n_series", 2))
    n_parallel = float(wind.get("n_parallel", 2))
    n_wires = float(geo.get("num_wires_per_slot", 14))
    I_rms = float(current_a)
    freq = rpm / 60.0 * pole_pairs
    omega_mech = 2 * math.pi * rpm / 60.0

    # ── Air-gap flux density via magnet circuit ──────────────────────────────
    h_mag = p.r_rotor_out - p.r_rotor_in          # radial magnet height [m]
    g = p.r_air_out - p.r_air_in                  # air gap [m]
    if h_mag <= 0:
        return DesignMetrics(feasible=False, reason="magnet height ≤ 0",
                             overrides=overrides or {})
    B_g = BR_MAG * h_mag / (h_mag + MU_R_MAG * CARTER_K * g)
    # pole-arc coverage modifies the fundamental amplitude
    alpha = min(max(p.magnet_fill_fraction, 0.05), 1.0)
    f_arc = math.sin(alpha * math.pi / 2.0)
    B_g1 = B_g * f_arc                            # fundamental air-gap flux [T]

    # ── Flux linkage & torque ────────────────────────────────────────────────
    r_ag = (p.r_air_out + p.r_air_in) / 2.0
    tau_p = 2 * math.pi * r_ag / p.num_poles
    N_branch = n_series * n_wires
    psi_pm = K_W * N_branch * (2 / math.pi) * B_g1 * tau_p * p.stack_length
    I_peak = I_rms * math.sqrt(2)
    I_q = I_peak * math.cos(math.radians(gamma_deg))
    T_em = K_TORQUE_CAL * 1.5 * pole_pairs * psi_pm * I_q
    P_mech = T_em * omega_mech

    # ── Iron losses (specific-loss × mass) ───────────────────────────────────
    N_slots = p.num_slots
    slot_pitch = math.pi * 2 * p.r_stator_in / N_slots
    tooth_w = float(geo.get("tooth_width", 9.2)) * 1e-3
    core_thick = float(geo.get("core_thickness", 4.2)) * 1e-3
    # raw flux density (for the saturation feasibility gate); clamped (for loss)
    B_tooth_raw = B_g * slot_pitch / max(tooth_w, 1e-4)
    B_back_raw = B_tooth_raw * tooth_w / max(2.0 * core_thick, 1e-4)
    B_tooth = min(2.0, B_tooth_raw)
    B_back = min(2.0, B_back_raw)
    B_stat_eff = math.sqrt((B_tooth**2 + B_back**2) / 2.0)
    masses = _masses(p, geo)
    P_fe_stat = K_ST * freq**1.6 * B_stat_eff**2 * masses["stator"]
    f_slot = freq * N_slots / pole_pairs
    B_rotor = min(1.4, B_g * 0.6)
    P_fe_rotor = K_ST * f_slot**1.6 * B_rotor**2 * masses["rotor"]
    P_fe = K_FE_CAL * (P_fe_stat + P_fe_rotor)

    # ── Magnet eddy (slot-harmonic) ──────────────────────────────────────────
    # P = K · σ · ω_slot² · B_ac² · d² · V / 24, the classic eddy-in-slab form.
    # Driven by slot-passing frequency² , the slot-harmonic air-gap flux, the
    # tangential magnet width (the slot ripple is tangential) and magnet volume.
    # (The exponential skin-penetration term was dropped — it over-attenuated by
    #  ~30× and its h_mag dependence was unreliable; K_MAG_CAL anchors the rest.)
    omega_slot = 2 * math.pi * f_slot
    B_mag_ac = min(B_g * (1 - tooth_w / slot_pitch) * 0.4, 0.25)   # slot harmonic [T]
    r_mid_mag = (p.r_rotor_out + p.r_rotor_in) / 2
    d_tang = 2 * math.pi * r_mid_mag / p.num_poles * p.magnet_fill_fraction
    d_eff = min(d_tang, h_mag)
    V_mag = math.pi * (p.r_rotor_out**2 - p.r_rotor_in**2) * p.stack_length * p.magnet_fill_fraction
    P_mag = K_MAG_CAL * p.sigma_mag * omega_slot**2 * B_mag_ac**2 * d_eff**2 * V_mag / 24.0

    # ── Copper loss ──────────────────────────────────────────────────────────
    P_cu = _copper_loss(p, geo, I_rms, n_parallel, coil_temp_c)

    P_loss = P_cu + P_fe + P_mag
    eff = P_mech / (P_mech + P_loss) if P_mech > 0 else 0.0
    m_tot = masses["total"]

    # saturation feasibility (heavily saturated tooth/back = unrealizable design)
    sat_ok = B_tooth_raw < B_SAT and B_back_raw < B_SAT
    reason = "" if sat_ok else (
        f"saturated (B_tooth={B_tooth_raw:.2f}, B_back={B_back_raw:.2f} > {B_SAT})")

    return DesignMetrics(
        feasible=sat_ok, reason=reason,
        T_em_Nm=T_em, efficiency=eff,
        torque_per_mass_Nm_kg=(T_em / m_tot if m_tot > 0 else 0.0),
        power_per_mass_W_kg=(P_mech / m_tot if m_tot > 0 else 0.0),
        P_mech_W=P_mech, P_loss_total_W=P_loss,
        P_cu_W=P_cu, P_fe_W=P_fe, P_mag_W=P_mag,
        mass_total_kg=m_tot, B_gap_T=B_g, B_tooth_T=B_tooth_raw, B_back_T=B_back_raw,
        overrides=overrides or {},
    )
