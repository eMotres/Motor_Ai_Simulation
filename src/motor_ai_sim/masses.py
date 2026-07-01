"""Single source of truth for motor component masses.

EVERY mass computation in the app must go through ``compute_masses`` so the mass —
and therefore torque-density (N·m/kg) and the iron-loss base — is IDENTICAL in
Simulation, the parameter sweep, and all three optimizers (descent / surrogate /
scan all reach it via optimization.design_eval).

Stator iron = back-iron YOKE + TEETH, where the teeth scale with ``tooth_width``.
The previous "(full stator disk − rectangular slots)" form was parametrised by
``slot_width`` and contained NO ``tooth_width`` term, so a tooth-width sweep left the
mass constant — torque-density then wrongly rewarded ever-wider teeth (sweep optimum
3.5 mm vs Ansys 3.1 mm).  With the teeth counted explicitly, widening a tooth adds
iron mass, so N·m/kg reflects the real iron-vs-torque trade-off.

Densities (active materials):
  silicon steel 20SW1200 : 7650 kg/m³   copper : 8900   F45SH NdFeB : 7500   Al6061 : 2700
"""
from __future__ import annotations
import math
from typing import Any, Dict

RHO_STEEL = 7650.0
RHO_CU    = 8900.0
RHO_MAG   = 7500.0
RHO_AL    = 2700.0


def end_winding_factor(p: Any, geo: Dict[str, Any] | None = None) -> float:
    """Canonical end-winding length factor  k_end = (active + end-turns) / active.

    SINGLE SOURCE — the copper MASS, phase RESISTANCE and copper LOSS must all scale
    the active (in-slot, = stack-length) copper by THIS factor, so they stay
    consistent.  ``fem_solver_2d.end_winding_factor_geom`` delegates here so the
    solver's loss/R use the exact same number.

    Tooth-coil (fractional-slot concentrated) winding: each axial end-turn is a
    half-loop joining the two coil sides that flank the wound tooth.  Its span is the
    coil-side centre-to-centre distance = (slot_width/2 + tooth_width + slot_width/2)
    = ``tooth_width + slot_width`` — so the loop length per side = π·span/2 and
        k_end = (π·(tooth_width + slot_width)/2 + L_stack) / L_stack.
    Crucially this GROWS with ``tooth_width``: a wider tooth lengthens the loop, so
    copper mass / R / loss all rise with the tooth (slope π/2L per mm), penalising
    over-wide teeth in the torque-density sweep — matching Ansys.  (The previous
    ``tooth = slot_pitch − slot_width`` form tracked slot_width only and was frozen
    under a tooth-width sweep, wrongly rewarding ever-wider teeth.)"""
    L = float(p.stack_length)
    if L <= 0:
        return 1.0
    slot_w  = float(p.slot_width_m)
    tooth_w = float((geo or {}).get("tooth_width", 0.0)) * 1e-3      # geo is in mm
    if tooth_w <= 0.0:                                               # no geo → derive
        r_mid = p.r_stator_in + p.slot_height_m * 0.5
        tau   = 2.0 * math.pi * r_mid / max(int(p.num_slots), 1)
        tooth_w = max(tau - slot_w, 0.3 * tau)
    span = tooth_w + slot_w                  # coil-side centre-to-centre over the tooth
    return (math.pi * span / 2.0 + L) / L


def compute_masses(p: Any, geo: Dict[str, Any], k_end: float = 0.0) -> Dict[str, Any]:
    """Component masses [kg] from the resolved params ``p`` (metres) + the geometry
    config ``geo`` (mm).  ``p`` must expose: stack_length, num_slots, slot_height_m,
    r_stator_out, r_stator_in, r_rotor_out, r_rotor_in, r_shaft_in,
    magnet_fill_fraction.  ``geo`` supplies tooth_width / num_wires_per_slot /
    wire_width / wire_height.  Returns masses + volumes + the densities used."""
    L  = float(p.stack_length)
    mm = 1e-3
    ns = int(p.num_slots)
    tooth_w = float(geo.get("tooth_width", 0.0)) * mm
    slot_h  = float(p.slot_height_m)
    n_wires = float(geo.get("num_wires_per_slot", 14))
    wire_w  = float(geo.get("wire_width", 5.0)) * mm
    wire_h  = float(geo.get("wire_height", 0.6)) * mm

    # ── Stator iron = back-iron yoke + teeth (teeth ∝ tooth_width) ─────────────
    r_slot_bottom = p.r_stator_in + slot_h
    V_yoke   = math.pi * (p.r_stator_out ** 2 - r_slot_bottom ** 2) * L
    V_teeth  = ns * tooth_w * slot_h * L
    V_stator = max(V_yoke + V_teeth, 0.0)
    m_stator = V_stator * RHO_STEEL

    # ── Copper: active in-slot copper × the SINGLE end-winding factor ─────────
    # V_cu = (in-slot copper) · k_end — the SAME factor the solver uses for phase
    # resistance and copper loss (copper_loss_W), so mass / R / loss stay consistent.
    # (The old "L + 2·L_endturn" distributed-winding form over-counted by ~k_end.)
    wire_area = wire_w * wire_h
    k_end_eff = float(k_end) if (k_end and k_end > 0) else end_winding_factor(p, geo)
    V_cu = ns * wire_area * n_wires * L * k_end_eff
    m_cu = V_cu * RHO_CU

    # ── Magnets / rotor back-iron / shaft ─────────────────────────────────────
    V_mag = math.pi * (p.r_rotor_out ** 2 - p.r_rotor_in ** 2) * L * p.magnet_fill_fraction
    m_mag = V_mag * RHO_MAG
    V_rotor = max(math.pi * (p.r_rotor_in ** 2 - p.r_shaft_in ** 2) * L, 0.0)
    m_rotor = V_rotor * RHO_STEEL
    V_shaft = math.pi * p.r_shaft_in ** 2 * L
    m_shaft = V_shaft * RHO_AL

    m_total = m_stator + m_cu + m_mag + m_rotor + m_shaft
    return {
        "stator": m_stator, "cu": m_cu, "mag": m_mag, "rotor": m_rotor,
        "shaft": m_shaft, "total": m_total,
        "V_stator": V_stator, "V_yoke": V_yoke, "V_teeth": V_teeth,
        "V_cu": V_cu, "V_mag": V_mag, "V_rotor": V_rotor, "V_shaft": V_shaft,
        "k_end": k_end_eff,
        "RHO": {"steel": RHO_STEEL, "cu": RHO_CU, "mag": RHO_MAG, "al": RHO_AL},
    }
