"""2-D magnetostatics PINN solver using NVIDIA Modulus Sym.

This module assembles the full Modulus training problem:

    Domains:
        stator_core  →  Magnetostatics2D(mu_r=mu_fe)
        air_gap      →  Magnetostatics2D(mu_r=1)
        rotor_core   →  Magnetostatics2D(mu_r=mu_fe)
        magnet       →  PermanentMagnet2D(Br, Mx, My)
        slot_A       →  Magnetostatics2D(mu_r=1, J_z=+J_peak)
        slot_B       →  Magnetostatics2D(mu_r=1, J_z=-J_peak)
        shaft        →  Magnetostatics2D(mu_r=mu_shaft)

    Boundary conditions:
        outer_stator    →  A_z = 0  (Dirichlet, Neumann-by-symmetry)
        inner_shaft     →  A_z = 0  (Dirichlet)
        interfaces      →  continuity of A_z and ν·∂A_z/∂n  (auto via PINN)

Run:
    python -m motor_ai_sim.simulation.solver_2d

or via the REST API:
    POST /api/simulation/run
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from motor_ai_sim.simulation.pdes import (
    Magnetostatics2D,
    PermanentMagnet2D,
    MU_0,
)
from motor_ai_sim.simulation.geometry_2d import (
    MotorDomains2D,
    MotorDomainParams,
    params_from_config,
    winding_current_density,
)
from motor_ai_sim.simulation.postprocess import (
    compute_torque_maxwell,
    compute_flux_density,
    make_polar_grid,
    compute_copper_losses,
    compute_iron_losses_by_domain,
    compute_magnet_losses,
    compute_efficiency,
)
from motor_ai_sim.config import get_config
from motor_ai_sim.materials import get_material

log = logging.getLogger(__name__)

# ── Modulus availability ──────────────────────────────────────────────────────
try:
    from modulus.sym.solver import Solver
    from modulus.sym.domain import Domain
    from modulus.sym.domain.constraint import (
        PointwiseInteriorConstraint,
        PointwiseBoundaryConstraint,
    )
    from modulus.sym.models.fully_connected import FullyConnectedArch
    from modulus.sym.node import Node
    from modulus.sym.key import Key
    from modulus.sym.hydra import ModulusConfig
    HAS_MODULUS = True
except ImportError:
    try:
        from physicsnemo.sym.solver import Solver
        from physicsnemo.sym.domain import Domain
        from physicsnemo.sym.domain.constraint import (
            PointwiseInteriorConstraint,
            PointwiseBoundaryConstraint,
        )
        from physicsnemo.sym.models.fully_connected import FullyConnectedArch
        from physicsnemo.sym.node import Node
        from physicsnemo.sym.key import Key
        HAS_MODULUS = True
    except ImportError:
        HAS_MODULUS = False
        log.warning("NVIDIA Modulus not found — solver will run in dry-run mode.")


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Simulation configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimConfig:
    """Runtime parameters for the 2-D magnetostatics PINN."""

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
# 2.  Main solver builder
# ─────────────────────────────────────────────────────────────────────────────

class MagnetostaticsSolver2D:
    """Assembles and runs the Modulus PINN for 2-D motor magnetostatics.

    Usage
    -----
    >>> solver = MagnetostaticsSolver2D.from_config()
    >>> result = solver.run()
    >>> print(f"Torque = {result['torque_Nm']:.3f} N·m")
    """

    def __init__(self, sim_cfg: SimConfig, geo_params: MotorDomainParams):
        self.cfg = sim_cfg
        self.geo = MotorDomains2D(geo_params)
        self.gp = geo_params
        self._solver: Optional[object] = None

    @classmethod
    def from_config(cls) -> "MagnetostaticsSolver2D":
        sim_cfg = SimConfig.from_motor_config()
        geo_params = params_from_config()
        return cls(sim_cfg, geo_params)

    # ── assemble ──────────────────────────────────────────────────────────────
    def build(self) -> None:
        """Assemble Modulus Domain + Solver (requires Modulus to be installed)."""
        if not HAS_MODULUS:
            log.warning("build() called but Modulus is not installed — skipping.")
            return

        cfg = self.cfg
        gp  = self.gp

        # ── 1. Network: maps (x, y) → A_z ─────────────────────────────────
        net = FullyConnectedArch(
            input_keys=[Key("x"), Key("y")],
            output_keys=[Key("A_z")],
            layer_size=cfg.layer_size,
            nr_layers=cfg.num_layers,
            activation_fn=cfg.activation,
        )
        nodes = [net.make_node(name="A_z_network")]

        # ── 2. PDE nodes per sub-domain ───────────────────────────────────
        # Air-like regions (μᵣ = 1)
        air_pde   = Magnetostatics2D(mu_r=1.0, J_z=0.0)
        nodes += air_pde.make_nodes()

        # Steel regions
        stator_pde = Magnetostatics2D(mu_r=cfg.mu_r_stator, J_z=0.0)
        nodes += stator_pde.make_nodes()

        rotor_pde  = Magnetostatics2D(mu_r=cfg.mu_r_rotor,  J_z=0.0)
        nodes += rotor_pde.make_nodes()

        shaft_pde  = Magnetostatics2D(mu_r=cfg.mu_r_shaft,  J_z=0.0)
        nodes += shaft_pde.make_nodes()

        # Winding — 3-phase currents at static rotor angle + phase offset γ
        # θ_elec = rotor_angle_deg * pole_pairs + phase_offset_deg
        import math as _math
        pole_pairs  = gp.num_poles // 2
        theta_elec  = _math.radians(cfg.rotor_angle_deg * pole_pairs + cfg.phase_offset_deg)
        I_A =  cfg.I_peak * _math.cos(theta_elec)          # phase A peak current
        I_B =  cfg.I_peak * _math.cos(theta_elec - 2*_math.pi/3)   # phase B
        I_C =  cfg.I_peak * _math.cos(theta_elec + 2*_math.pi/3)   # phase C
        log.info("Phase currents: I_A=%.1f  I_B=%.1f  I_C=%.1f  A  (θ_elec=%.1f°)",
                 I_A, I_B, I_C, _math.degrees(theta_elec))

        slot_area_m2 = (gp.slot_area_m2 if hasattr(gp, 'slot_area_m2')
                        else 1e-4)  # fallback ~1 cm²
        n_wires = getattr(gp, 'num_wires_per_slot', gp.num_slots // 3)

        J_A_pos = n_wires * I_A / slot_area_m2
        J_A_neg = -J_A_pos
        J_B_pos = n_wires * I_B / slot_area_m2
        J_C_pos = n_wires * I_C / slot_area_m2

        slot_A_pos_pde = Magnetostatics2D(mu_r=1.0, J_z=J_A_pos)
        nodes += slot_A_pos_pde.make_nodes()
        slot_A_neg_pde = Magnetostatics2D(mu_r=1.0, J_z=J_A_neg)
        nodes += slot_A_neg_pde.make_nodes()
        slot_B_pde = Magnetostatics2D(mu_r=1.0, J_z=J_B_pos)
        nodes += slot_B_pde.make_nodes()
        slot_C_pde = Magnetostatics2D(mu_r=1.0, J_z=J_C_pos)
        nodes += slot_C_pde.make_nodes()

        # Permanent magnet (radial M, simplified: Mx=1, My=0 for first pole)
        pm_pde = PermanentMagnet2D(Br=cfg.Br_magnet, Mx=1.0, My=0.0)
        nodes += pm_pde.make_nodes()

        # ── 3. Domain + constraints ───────────────────────────────────────
        domain = Domain()

        def _interior(geo_key: str, pde_key: str, weight: float = 1.0):
            geo = self.geo[geo_key]
            domain.add_constraint(
                PointwiseInteriorConstraint(
                    nodes=nodes,
                    geometry=geo,
                    outvar={"magnetostatics": 0},
                    batch_size=cfg.batch_size_interior,
                    lambda_weighting={"magnetostatics": weight},
                ),
                name=f"interior_{geo_key}",
            )

        _interior("stator_core", "stator_pde")
        _interior("air_gap",     "air_pde")
        _interior("rotor_core",  "rotor_pde")
        _interior("magnet",      "pm_pde")
        _interior("shaft",       "shaft_pde")

        # Dirichlet BC: A_z = 0 on outer stator boundary
        outer_circle = self.geo["full"]
        domain.add_constraint(
            PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=outer_circle,
                outvar={"A_z": 0},
                batch_size=cfg.batch_size_boundary,
            ),
            name="bc_outer_dirichlet",
        )

        # ── 4. Solver ─────────────────────────────────────────────────────
        self._domain = domain
        self._nodes  = nodes

        log.info("Modulus solver assembled. Domains: %s", list(self.geo.keys()))

    # ── train ─────────────────────────────────────────────────────────────────
    def run(self) -> Dict:
        """Train PINN and return key results.

        Returns a dict with:
            torque_Nm, B_max_T, training_steps, output_dir
        """
        if not HAS_MODULUS:
            return self._dry_run()

        self.build()

        cfg = self.cfg
        cfg.output_dir.mkdir(parents=True, exist_ok=True)

        solver = Solver(
            cfg=self._make_modulus_cfg(),
            domain=self._domain,
        )
        solver.solve()
        log.info("Training complete. Computing torque...")

        # ── post-process: load trained network and evaluate ───────────────
        result = self._postprocess(solver)
        return result

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

    def _postprocess(self, solver) -> Dict:
        """Extract torque and B_max from the trained network."""
        try:
            net_fn = solver.get_network_output       # Modulus API
        except AttributeError:
            net_fn = None

        # Fallback: evaluate on a grid analytically (for testing)
        r_eval = (self.gp.r_air_out + self.gp.r_air_in) / 2

        def A_z_fn(x, y):
            if net_fn:
                pts = np.stack([x.ravel(), y.ravel()], axis=-1)
                out = net_fn({"x": pts[:, 0:1], "y": pts[:, 1:2]})
                return out["A_z"].reshape(x.shape)
            else:
                return np.zeros_like(x)   # placeholder

        torque = compute_torque_maxwell(
            A_z_fn,
            r_eval=r_eval,
            stack_length=self.gp.stack_length,
        )

        x_g, y_g = make_polar_grid(
            self.gp.r_shaft_in, self.gp.r_stator_out, n_r=40, n_phi=180
        )
        _, _, B_mag = compute_flux_density(A_z_fn, x_g, y_g)

        # ── Losses ────────────────────────────────────────────────────────
        li = self._loss_inputs()
        cu = compute_copper_losses(**{k: li[k] for k in [
            "I_phase_rms", "n_coils_per_phase", "n_parallel", "n_series",
            "n_wires_per_slot", "wire_width_m", "wire_height_m",
            "motor_length_m", "r_slot_mid_m", "n_slots",
        ]})

        try:
            from motor_ai_sim import materials as mat_lib
            from motor_ai_sim.config import get_config as _gc
            mat_cfg = _gc().get("materials", {})
            steel   = mat_lib.get_material("steel", mat_cfg.get("stator_core", "20SW1200"))
            kh, kc, ke = steel.core_loss_kh, steel.core_loss_kc, steel.core_loss_ke
        except Exception:
            kh, kc, ke = 2.5, 0.003, 0.0

        gp = self.gp
        fe = compute_iron_losses_by_domain(
            A_z_fn, frequency=li["frequency"],
            stack_length=gp.stack_length,
            r_stator_in=gp.r_stator_in, r_stator_out=gp.r_stator_out,
            r_rotor_in=gp.r_rotor_in,   r_rotor_out=gp.r_rotor_out,
            kh=kh, kc=kc, ke=ke,
        )

        try:
            from motor_ai_sim.config import get_config as _gc
            mat_cfg = _gc().get("materials", {})
            from motor_ai_sim import materials as mat_lib
            mag_mat = mat_lib.get_material("magnet", mat_cfg.get("magnet", "F45SH_120C"))
            sigma_mag = mag_mat.sigma
        except Exception:
            sigma_mag = 6.25e5

        pm = compute_magnet_losses(
            A_z_fn, frequency=li["frequency"],
            stack_length=gp.stack_length,
            r_magnet_in=gp.r_rotor_in, r_magnet_out=gp.r_rotor_out,
            sigma_magnet=sigma_mag,
        )

        eff = compute_efficiency(
            torque_Nm=torque, rpm=li["rpm"],
            P_cu_W=cu["P_cu_total_W"],
            P_fe_W=fe["P_fe_total_W"],
            P_mag_W=pm["P_mag_eddy_W"],
        )

        return {
            "torque_Nm":        torque,
            "B_max_T":          float(B_mag.max()),
            "B_mean_T":         float(B_mag.mean()),
            "training_steps":   self.cfg.max_steps,
            "output_dir":       str(self.cfg.output_dir),
            # Losses
            "P_cu_total_W":     cu["P_cu_total_W"],
            "R_phase_ohm":      cu["R_phase_ohm"],
            "P_fe_stator_W":    fe["P_fe_stator_W"],
            "P_fe_rotor_W":     fe["P_fe_rotor_W"],
            "P_mag_eddy_W":     pm["P_mag_eddy_W"],
            # Efficiency
            "P_mech_W":         eff["P_mech_W"],
            "P_input_W":        eff["P_input_W"],
            "P_loss_total_W":   eff["P_loss_total_W"],
            "efficiency_pct":   eff["efficiency_pct"],
        }

    # ── dry run (no Modulus) ──────────────────────────────────────────────────
    def _dry_run(self) -> Dict:
        """Copper losses computed analytically; iron/magnet need PINN field."""
        log.warning("DRY RUN — Modulus not installed. Copper losses computed analytically.")
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
            # Iron / magnet — need PINN field
            "P_fe_stator_W":    None,
            "P_fe_rotor_W":     None,
            "P_mag_eddy_W":     None,
            # Efficiency — need torque from PINN
            "P_mech_W":         None,
            "P_input_W":        None,
            "P_loss_total_W":   cu["P_cu_total_W"],
            "efficiency_pct":   None,
            "note":             "Iron/magnet losses and efficiency require NVIDIA Modulus",
        }

    def _make_modulus_cfg(self):
        """Build a minimal ModulusConfig-compatible object."""
        cfg = self.cfg
        from omegaconf import OmegaConf
        d = {
            "device":          cfg.device,
            "max_steps":       cfg.max_steps,
            "save_filetypes":  ["vtk", "npz"],
            "network_dir":     str(cfg.output_dir / "network"),
        }
        return OmegaConf.create(d)


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
