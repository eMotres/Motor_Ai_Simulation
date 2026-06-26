"""2-D magnetostatics solver configuration and analytical (dry-run) results.

The NVIDIA Modulus PINN training path has been removed.  The active EM solver is
scikit-fem (see motor_ai_sim.simulation.fem_solver_2d).  This module retains the
operating-point configuration (SimConfig) and the analytical copper-loss /
efficiency "dry-run" result that the /api/simulation/run + /status endpoints
still consume.

Run:
    python -m motor_ai_sim.simulation.solver_2d

or via the REST API:
    POST /api/simulation/run
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from motor_ai_sim.simulation.geometry_2d import (
    MotorDomains2D,
    MotorDomainParams,
    params_from_config,
)
from motor_ai_sim.simulation.postprocess import (
    compute_copper_losses,
    compute_efficiency,
)
from motor_ai_sim.config import get_config
from motor_ai_sim.materials import get_material

log = logging.getLogger(__name__)

HAS_MODULUS = False  # NVIDIA Modulus path removed


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Simulation configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    """Runtime parameters for the 2-D magnetostatics solve."""

    # Operating point (read from motor_config.yaml by default)
    I_peak: float = 10.0           # Peak coil current [A]  = I_phase_peak / n_parallel
    frequency_hz: float = 50.0     # Electrical frequency [Hz]
    rpm: float = 2000.0            # Rotor speed [rpm]
    rotor_angle_deg: float = 0.0   # Fixed rotor position for static solve [°]
    phase_offset_deg: float = 0.0  # γ — current angle offset vs rotor d-axis [°]
                                   #   0° = d-axis (field on), 90° = q-axis (max torque SPMSM)

    # Network architecture
    layer_size: int = 128          # Neurons per hidden layer
    num_layers: int = 6            # Number of hidden layers
    activation: str = "tanh"       # Activation function

    # Training
    max_steps: int = 10_000        # Training iterations
    learning_rate: float = 1e-3
    batch_size_interior: int = 5_000
    batch_size_boundary: int = 1_000

    # Material reluctivities (overridden from materials library)
    mu_r_stator: float = 5000.0    # Silicon steel relative permeability
    mu_r_rotor: float = 5000.0
    mu_r_shaft: float = 1000.0
    Br_magnet: float = 1.2         # Remanent flux density [T]

    # Output
    output_dir: Path = Path("simulation_output/magnetostatics_2d")
    device: str = "cuda"           # "cuda" or "cpu"

    @classmethod
    def from_motor_config(cls) -> "SimConfig":
        """Load operating point from motor_config.yaml."""
        cfg = get_config()
        sim = cfg.get("simulation", {})
        mat = cfg.get("materials", {})

        # Try to read Br from materials library
        Br = 1.2
        try:
            mag = get_material("magnet", mat.get("magnet", "F45SH_120C"))
            Br = mag.Br
        except Exception:
            pass

        return cls(
            I_peak=sim.get("max_current", 10.0),
            frequency_hz=sim.get("frequency", 50.0),
            rpm=sim.get("rpm", 2000.0),
            phase_offset_deg=sim.get("phase_offset_deg", 0.0),
            Br_magnet=Br,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Solver
# ─────────────────────────────────────────────────────────────────────────────

class MagnetostaticsSolver2D:
    """Operating-point holder that returns analytical copper-loss / efficiency
    results.  (The NVIDIA Modulus PINN training path has been removed; the active
    field solve lives in motor_ai_sim.simulation.fem_solver_2d.)

    Usage
    -----
    >>> solver = MagnetostaticsSolver2D.from_config()
    >>> result = solver.run()
    """

    def __init__(self, sim_cfg: SimConfig, geo_params: MotorDomainParams):
        self.cfg = sim_cfg
        self.geo = MotorDomains2D(geo_params)
        self.gp = geo_params

    @classmethod
    def from_config(cls) -> "MagnetostaticsSolver2D":
        sim_cfg = SimConfig.from_motor_config()
        geo_params = params_from_config()
        return cls(sim_cfg, geo_params)

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> Dict:
        """Return the analytical (copper-loss / efficiency) result."""
        return self._dry_run()

    def _loss_inputs(self) -> dict:
        """Collect geometry + winding params needed for loss calculations.

        Current naming convention:
          YAML  max_current = I_phase_rms  [Arms]   (stored as RMS, phase)
          cfg.I_peak may be I_coil_peak (when sent from frontend) or
                           I_phase_rms  (when loaded from YAML directly).
          We always read I_phase_rms from YAML to be consistent.
        """
        cfg_yaml = get_config()
        g = cfg_yaml.get("geometry", {})
        w = cfg_yaml.get("winding", {})
        s = cfg_yaml.get("simulation", {})
        mm = 1e-3
        gp = self.gp
        r_slot_mid = gp.r_stator_in + (gp.r_stator_out - gp.r_stator_in) * 0.5
        # Always use I_phase_rms from YAML (ground truth)
        I_phase_rms = s.get("max_current", 85.0)   # Arms, phase
        return {
            "I_phase_rms":       I_phase_rms,
            "n_coils_per_phase": w.get("n_coils_per_phase", 4),
            "n_parallel":        w.get("n_parallel", 2),
            "n_series":          w.get("n_series", 2),
            "n_wires_per_slot":  int(g.get("num_wires_per_slot", 14)),
            "wire_width_m":      g.get("wire_width",  5.0) * mm,
            "wire_height_m":     g.get("wire_height", 0.6) * mm,
            "motor_length_m":    gp.stack_length,
            "r_slot_mid_m":      r_slot_mid,
            "n_slots":           gp.num_slots,
            "frequency":         self.cfg.frequency_hz,
            "rpm":               self.cfg.rpm,
        }

    def _dry_run(self) -> Dict:
        """Copper losses computed analytically; iron/magnet need a field solve."""
        log.warning("Analytical result — copper losses computed analytically.")
        domains = MotorDomains2D(self.gp)
        log.info("\n%s", domains.summary())

        li = self._loss_inputs()
        cu = compute_copper_losses(**{k: li[k] for k in [
            "I_phase_rms", "n_coils_per_phase", "n_parallel", "n_series",
            "n_wires_per_slot", "wire_width_m", "wire_height_m",
            "motor_length_m", "r_slot_mid_m", "n_slots",
        ]})
        log.info("Copper losses: %.1f W  (R_phase = %.4f Ω)", cu["P_cu_total_W"], cu["R_phase_ohm"])

        # Efficiency cannot be computed without torque
        eff = compute_efficiency(torque_Nm=0.0, rpm=li["rpm"],
                                  P_cu_W=cu["P_cu_total_W"])

        return {
            "torque_Nm":        0.0,
            "B_max_T":          0.0,
            "B_mean_T":         0.0,
            "training_steps":   0,
            "output_dir":       str(self.cfg.output_dir),
            "status":           "dry_run",
            "modulus_available": False,
            # Copper losses (analytical)
            "P_cu_total_W":     cu["P_cu_total_W"],
            "R_phase_ohm":      cu["R_phase_ohm"],
            "R_coil_ohm":       cu["R_coil_ohm"],
            "L_turn_mm":        cu["L_turn_mm"],
            "I_coil_rms_A":     cu["I_coil_rms_A"],
            # Iron / magnet — need a field solve
            "P_fe_stator_W":    None,
            "P_fe_rotor_W":     None,
            "P_mag_eddy_W":     None,
            # Efficiency — needs torque from a field solve
            "P_mech_W":         None,
            "P_input_W":        None,
            "P_loss_total_W":   cu["P_cu_total_W"],
            "efficiency_pct":   None,
            "note":             "Iron/magnet losses and efficiency require the FEM field solver",
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    solver = MagnetostaticsSolver2D.from_config()
    result = solver.run()
    print("\n=== 2-D Magnetostatics result ===")
    for k, v in result.items():
        print(f"  {k:25s}: {v}")


if __name__ == "__main__":
    main()
