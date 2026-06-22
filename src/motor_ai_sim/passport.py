"""Generate an analytical PASSPORT of a motor from FEM.

Merges the two extractor scripts into one reusable function so the admin can
characterise any catalog motor and publish a passport the Configurator scales
INSTANTLY (no FEM):

  • scaling base — 3 transient solves of the active config
      A) loaded  (I0, gamma, eddy ON) -> T0, R0, Pfe0, Pmag0, mass0
      B) no-load (I=0)                -> Vemf0_peak
      C) loaded @1.5*L0               -> R split (R_active prop. L + R_end const)
    (mirrors extract_passport.py)
  • speed curves — Pfe / Pmag vs rpm sweep (mirrors extract_speed_curves.py)

The returned dict is { passport, fit, geo, poles, slots } and matches
ReferenceMotor in web/src/lib/referencePassports.ts.  It operates on the ACTIVE
config, so the caller loads the target motor's preset first (apply_preset).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_RPMS: List[float] = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]


def generate_passport(
    *,
    I0: Optional[float] = None,
    gamma_deg: Optional[float] = None,
    rpms: Optional[List[float]] = None,
    base_steps: int = 12,
    sweep_steps: int = 12,
    mesh_size_mm: float = 5.0,
) -> Dict[str, Any]:
    """Run the FEM passport extraction on the active config → full passport dict.

    I0 / gamma_deg default to the MOTOR's own operating point (config.simulation
    max_current / phase_offset_deg) — not a hard-coded reference point — so the
    base solve produces the motor's real torque.  Pass explicit values to override.
    """
    import json
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes.simulation import get_fem_transient

    cfg = get_config()
    g = cfg.get("geometry", {}) or {}
    wind = cfg.get("winding", {}) or {}
    sim = cfg.setdefault("simulation", {})
    # Operating point from the motor itself (its loaded preset), not hard-coded:
    # max_current = phase RMS [A], phase_offset_deg = load angle gamma [deg].
    if I0 is None:
        I0 = float(sim.get("max_current", 110.0) or 110.0)
    if gamma_deg is None:
        gamma_deg = float(sim.get("phase_offset_deg", 28.0) or 28.0)

    N0 = float(g.get("num_wires_per_slot", 0) or 0)
    L0 = float(g.get("motor_length", 0) or 0)
    wireH0 = float(g.get("wire_height", 0.0) or 0.0)
    nP0 = float(wind.get("n_parallel", 2) or 2)

    def run(I: float, length: Optional[float] = None, eddy: bool = False, steps: int = 12):
        ov: Dict[str, float] = {}
        if length is not None:
            ov["motor_length"] = round(length, 3)
        return get_fem_transient(
            n_steps_per_period=steps, n_periods=1.0, gamma_deg=gamma_deg,
            I_phase_rms=I, mesh_size_mm=mesh_size_mm, n_sectors=4,
            sliding_band=True, rotor_eddy=eddy,
            geo=(json.dumps(ov) if ov else None),
        )

    # ── scaling base: A loaded, B no-load, C @1.5L ──────────────────────────
    A = run(I0, eddy=True, steps=base_steps)
    B = run(0.0, eddy=False, steps=base_steps)
    C = run(I0, length=L0 * 1.5, eddy=False, steps=max(4, base_steps // 3))

    sa = A.get("summary", {}) or {}
    T0 = float(A.get("T_avg_Nm", 0.0) or 0.0)
    R_A = float(A.get("R_phase_ohm", 0.0) or 0.0)
    R_C = float(C.get("R_phase_ohm", 0.0) or 0.0)
    Vemf0 = float(B.get("V_peak", 0.0) or 0.0)
    rpm0 = float(A.get("rpm", 0.0) or 0.0)
    # R(L) = R_active*(L/L0) + R_end ; R_C @1.5L0 -> R_C - R_A = 0.5*R_active
    R_active = 2.0 * (R_C - R_A)
    R_end = max(0.0, R_A - R_active)
    endWindFrac = min(1.0, max(0.0, R_end / R_A)) if R_A > 0 else 0.0

    # ── speed sweep: Pfe / Pmag vs rpm (rpm read from cfg; save+restore) ─────
    rpms = rpms or DEFAULT_RPMS
    rpm_saved = sim.get("rpm")
    speed: Dict[str, List[float]] = {"rpm": [], "Pfe_W": [], "Pmag_W": []}
    try:
        for r in rpms:
            sim["rpm"] = r
            d = get_fem_transient(
                n_steps_per_period=sweep_steps, n_periods=1.0, gamma_deg=gamma_deg,
                I_phase_rms=I0, mesh_size_mm=mesh_size_mm + 1.0, n_sectors=4,
                sliding_band=True, rotor_eddy=True, fresh=True,
            )

            def gv(k: str) -> float:
                v = d.get(k)
                if v is None:
                    return 0.0
                if isinstance(v, (list, tuple)):
                    return float(sum(v) / len(v)) if v else 0.0
                return float(v)

            speed["rpm"].append(round(float(d.get("rpm") or r)))
            speed["Pfe_W"].append(round(gv("P_fe_W"), 1))
            speed["Pmag_W"].append(round(gv("P_mag_eddy_W") + gv("P_shaft_eddy_W"), 1))
    finally:
        if rpm_saved is not None:
            sim["rpm"] = rpm_saved
        else:
            sim.pop("rpm", None)

    passport = {
        "N0": N0, "L0_mm": L0, "wireH0_mm": wireH0,
        "I0_A": I0, "rpm0": round(rpm0), "nP0": nP0,
        "T0_Nm": round(T0, 3),
        "Vemf0_peak_V": round(Vemf0, 2),
        "Vload0_peak_V": round(float(sa.get("V_phase_peak_V") or 0.0), 2),
        "R0_ohm": round(R_A, 5),
        "endWindFrac": round(endWindFrac, 3),
        "Pfe0_W": round(float(sa.get("P_core_W") or 0.0), 2),
        "Pmag0_W": round(float(sa.get("P_solid_W") or 0.0), 2),
        "mass0_kg": round(float(sa.get("mass_total_kg") or 0.0), 3),
        "speed": speed,
    }
    fit = {
        "slotHeight_mm": float(g.get("slot_height", 0.0) or 0.0),
        "insulation_mm": float(g.get("insulation_thickness", 0.0) or 0.0),
        "wireSpacingY_mm": float(g.get("wire_spacing_y", 0.0) or 0.0),
        "slotWidth_mm": float(g.get("slot_width", 0.0) or 0.0),
        "wireWidth_mm": float(g.get("wire_width", 0.0) or 0.0),
    }
    geo = {
        "statorOR_mm": float(g.get("stator_outer_radius", 0.0) or 0.0),
        "statorIR_mm": float(g.get("stator_inner_radius", 0.0) or 0.0),
        "rotorOR_mm": float(g.get("rotor_outer_radius", 0.0) or 0.0),
        "rotorIR_mm": float(g.get("rotor_inner_radius", 0.0) or 0.0),
        "numSlots": int(g.get("num_slots", 0) or 0),
        "numPoles": int(g.get("num_poles", 0) or 0),
        "magnetHeight_mm": float(g.get("magnet_height", 0.0) or 0.0),
    }
    return {"passport": passport, "fit": fit, "geo": geo,
            "poles": geo["numPoles"], "slots": geo["numSlots"]}
