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
    MagnetostaticsAC2D,
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
            PointwiseConstraint,
        )
        from physicsnemo.sym.dataset import DictPointwiseDataset
        from physicsnemo.sym.loss import PointwiseLossNorm
        from physicsnemo.sym.models.fully_connected import FullyConnectedArch
        from physicsnemo.sym.node import Node
        from physicsnemo.sym.key import Key
        from physicsnemo.sym.models.activation import Activation
        HAS_MODULUS = True
    except ImportError:
        HAS_MODULUS = False
        log.warning("NVIDIA Modulus not found — solver will run in dry-run mode.")


def _act_enum(name):
    """physicsnemo-sym 2.4 wants an Activation enum, not a string like 'tanh'
    (old Modulus Sym accepted strings).  Map our config string → enum; fall back
    to the raw string if the enum isn't importable (older Modulus)."""
    try:
        return getattr(Activation, str(name).upper())
    except (NameError, AttributeError):
        return name


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
        """Assemble Modulus Domain + Solver (requires Modulus to be installed).

        Uses frequency-domain AC magnetostatics (MagnetostaticsAC2D) for all
        domains.  Network outputs Ar(x,y) and Ai(x,y) — real and imaginary
        parts of the vector-potential phasor A_z = Ar + j·Ai.

        This captures:
          - skin effect and proximity effect in copper conductors (σ_cu)
          - eddy current losses in NdFeB magnets (σ_mag)
          - B-field distribution for Bertotti iron losses in steel (σ=0)
        """
        if not HAS_MODULUS:
            log.warning("build() called but Modulus is not installed — skipping.")
            return

        cfg = self.cfg
        gp  = self.gp

        omega = 2 * math.pi * cfg.frequency_hz   # electrical angular frequency

        # ── 1. Network: maps (x, y) → [Ar, Ai] ───────────────────────────
        net = FullyConnectedArch(
            input_keys=[Key("x"), Key("y")],
            output_keys=[Key("Ar"), Key("Ai")],
            layer_size=cfg.layer_size,
            nr_layers=cfg.num_layers,
            activation_fn=_act_enum(cfg.activation),
        )
        nodes = [net.make_node(name="A_phasor_network")]

        # ── 2. Phase currents at this rotor angle ──────────────────────────
        pole_pairs = gp.num_poles // 2
        theta_elec = math.radians(cfg.rotor_angle_deg * pole_pairs
                                  + cfg.phase_offset_deg)
        I_phase = {
            'A':  cfg.I_peak * math.cos(theta_elec),
            'B':  cfg.I_peak * math.cos(theta_elec - 2 * math.pi / 3),
            'C':  cfg.I_peak * math.cos(theta_elec + 2 * math.pi / 3),
        }
        log.info(
            "Phase currents: I_A=%.1f  I_B=%.1f  I_C=%.1f A  (theta_elec=%.1f deg)",
            I_phase['A'], I_phase['B'], I_phase['C'],
            math.degrees(theta_elec),
        )

        slot_area = gp.slot_width_m * gp.slot_height_m * gp.fill_factor

        # ── 3. Bulk-domain PDE nodes ───────────────────────────────────────
        # Laminated steel: σ=0 (Bertotti handles iron losses in post-process)
        stator_pde = MagnetostaticsAC2D(mu_r=cfg.mu_r_stator, sigma=0.0, omega=omega)
        nodes += stator_pde.make_nodes()

        rotor_pde  = MagnetostaticsAC2D(mu_r=cfg.mu_r_rotor,  sigma=0.0, omega=omega)
        nodes += rotor_pde.make_nodes()

        shaft_pde  = MagnetostaticsAC2D(mu_r=cfg.mu_r_shaft,  sigma=0.0, omega=omega)
        nodes += shaft_pde.make_nodes()

        air_pde    = MagnetostaticsAC2D(mu_r=1.0,              sigma=0.0, omega=omega)
        nodes += air_pde.make_nodes()

        # ── 4. Domain ─────────────────────────────────────────────────────
        domain = Domain()
        _outvars_zero = {"magnetostatics_ac_real": 0, "magnetostatics_ac_imag": 0}

        def _add_interior(name: str, geo, weight: float = 1.0):
            """Add constraint using Modulus CSG geometry."""
            domain.add_constraint(
                PointwiseInteriorConstraint(
                    nodes=nodes,
                    geometry=geo,
                    outvar=_outvars_zero,
                    batch_size=cfg.batch_size_interior,
                    lambda_weighting={k: weight for k in _outvars_zero},
                ),
                name=f"interior_{name}",
            )

        def _add_pointwise(name: str, numpy_domain, n_pts: int = 2000,
                           weight: float = 1.0):
            """Add constraint using pre-sampled points from numpy domain.

            Used for slots/magnets that aren't native Modulus CSG objects.
            """
            pts = numpy_domain.sample_interior(n_pts)
            area_val = float(np.prod([
                np.ptp(pts["x"]) if np.ptp(pts["x"]) > 0 else 1.0,
                np.ptp(pts["y"]) if np.ptp(pts["y"]) > 0 else 1.0,
            ])) / n_pts
            invar = {
                "x": pts["x"].reshape(-1, 1),
                "y": pts["y"].reshape(-1, 1),
            }
            outvar = {
                "magnetostatics_ac_real": np.zeros((n_pts, 1)),
                "magnetostatics_ac_imag": np.zeros((n_pts, 1)),
            }
            lam = {k: np.full((n_pts, 1), weight) for k in outvar}
            dataset = DictPointwiseDataset(invar=invar, outvar=outvar,
                                           lambda_weighting=lam)
            domain.add_constraint(
                PointwiseConstraint(
                    nodes=nodes,
                    dataset=dataset,
                    loss=PointwiseLossNorm(),
                    batch_size=min(cfg.batch_size_interior, n_pts),
                    shuffle=True,
                    drop_last=False,
                    num_workers=0,
                ),
                name=f"interior_{name}",
            )

        _add_interior("stator_core", self.geo["stator_core"])
        _add_interior("air_gap",     self.geo["air_gap"])
        _add_interior("rotor_core",  self.geo["rotor_core"])
        _add_interior("shaft",       self.geo["shaft"])

        # ── 5. Per-slot conductor constraints (24 slots) ───────────────────
        slot_pde_cache: Dict[tuple, object] = {}
        for slot_name, slot_geo, (phase, direction) in self.geo.slot_domains():
            J_z = direction * I_phase[phase] * gp.num_wires_per_slot / slot_area
            key = (phase, direction)
            if key not in slot_pde_cache:
                pde = MagnetostaticsAC2D(
                    mu_r=1.0, sigma=gp.sigma_cu, omega=omega, Jr=J_z,
                )
                slot_pde_cache[key] = pde
                nodes += pde.make_nodes()
            _add_pointwise(slot_name, slot_geo, n_pts=1000)

        # ── 6. Per-magnet constraints (28 poles) ──────────────────────────
        # TANGENTIAL magnetization along the magnet bottom edge, alternating
        # with polarity — same model as the analytical Green's-function solver
        # in routes/simulation.py::get_field2d.
        pole_pitch = 2 * math.pi / gp.num_poles
        for mag_name, mag_geo, polarity in self.geo.magnet_domains():
            i = int(mag_name.split("_")[1])
            phi_c = (i + 0.5) * pole_pitch
            # Tangent (+φ̂) at angle φ_c is (-sin(φ_c), cos(φ_c))
            Mx = polarity * (-math.sin(phi_c))
            My = polarity * (+math.cos(phi_c))
            pm_ac_pde = MagnetostaticsAC2D(
                mu_r=1.05, sigma=gp.sigma_mag, omega=omega, Jr=0.0,
            )
            nodes += pm_ac_pde.make_nodes()
            _add_pointwise(mag_name, mag_geo, n_pts=500)
            self.geo.material_props[mag_name].update(
                {"Mx": Mx, "My": My, "Br": cfg.Br_magnet}
            )

        # ── 7. Dirichlet BC: Ar = Ai = 0 on outer stator ─────────────────
        domain.add_constraint(
            PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=self.geo["full"],
                outvar={"Ar": 0, "Ai": 0},
                batch_size=cfg.batch_size_boundary,
            ),
            name="bc_outer_dirichlet",
        )

        self._domain = domain
        self._nodes  = nodes
        log.info("Modulus solver assembled (AC phasor). omega=%.1f rad/s, "
                 "%d slot + %d magnet constraints.",
                 omega, gp.num_slots, gp.num_poles)

    # ── train ─────────────────────────────────────────────────────────────────
    def run(self) -> Dict:
        """Train PINN and return key results."""
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
        """Compute all losses and torque from the trained AC phasor network.

        The network outputs Ar(x,y), Ai(x,y) — real/imaginary parts of
        A_z phasor.  From these we derive:

          B field      : B_x = dAr/dy + j dAi/dy,  B_y = -dAr/dx - j dAi/dx
          Copper losses: from J_total = J_source - jωσ·A_z distribution
          Magnet eddy  : P = (ω²σ/2) ∫|A_z|² dV  (classical formula)
          Iron losses  : Bertotti on |B|_rms from PINN B field
          Torque       : Maxwell stress tensor on air-gap circle
        """
        try:
            net_fn = solver.get_network_output
        except AttributeError:
            net_fn = None

        gp    = self.gp
        omega = 2 * math.pi * self.cfg.frequency_hz

        def _eval(x: np.ndarray, y: np.ndarray) -> tuple:
            """Return (Ar, Ai) arrays evaluated at (x, y) points."""
            if net_fn is not None:
                out = net_fn({"x": x.reshape(-1, 1), "y": y.reshape(-1, 1)})
                return out["Ar"].ravel(), out["Ai"].ravel()
            return np.zeros_like(x), np.zeros_like(y)

        # ── Build evaluation grid ─────────────────────────────────────────
        x_g, y_g = make_polar_grid(gp.r_shaft_in, gp.r_stator_out,
                                   n_r=40, n_phi=180)
        Ar_g, Ai_g = _eval(x_g, y_g)
        # Reconstruct |A_z|² and |B|_rms on the grid
        A_mag2 = Ar_g**2 + Ai_g**2         # |A_z phasor|²

        # A_z_fn returning real part (for Maxwell torque — uses peak field)
        def A_z_fn(x, y):
            Ar, _ = _eval(x, y)
            return Ar.reshape(x.shape)

        _, _, B_mag = compute_flux_density(A_z_fn, x_g, y_g)

        # ── Torque (Maxwell stress on air-gap midline) ─────────────────────
        r_eval = (gp.r_air_out + gp.r_air_in) / 2
        torque = compute_torque_maxwell(A_z_fn, r_eval=r_eval,
                                        stack_length=gp.stack_length)

        # ── Copper losses — AC (skin + proximity from PINN field) ─────────
        # J_total = J_source − jωσ_cu · A_z
        # P_cu = (1/(2σ_cu)) · stack_length · Σ_slots ∫|J_total|² dA
        li = self._loss_inputs()
        slot_area = gp.slot_width_m * gp.slot_height_m * gp.fill_factor
        P_cu_ac = 0.0
        pole_pairs = gp.num_poles // 2
        theta_elec = math.radians(self.cfg.rotor_angle_deg * pole_pairs
                                  + self.cfg.phase_offset_deg)
        I_phase = {
            'A': self.cfg.I_peak * math.cos(theta_elec),
            'B': self.cfg.I_peak * math.cos(theta_elec - 2 * math.pi / 3),
            'C': self.cfg.I_peak * math.cos(theta_elec + 2 * math.pi / 3),
        }
        for _, slot_geo, (phase, direction) in self.geo.slot_domains():
            J_src = direction * I_phase[phase] * gp.num_wires_per_slot / slot_area
            pts = slot_geo.sample_interior(n=2000)
            xs = pts["x"].ravel()
            ys = pts["y"].ravel()
            Ar_s, Ai_s = _eval(xs, ys)
            # J_total_re = J_src − ω σ_cu Ai,  J_total_im = −ω σ_cu Ar
            J_re = J_src - omega * gp.sigma_cu * Ai_s
            J_im = -omega * gp.sigma_cu * Ar_s
            J2   = J_re**2 + J_im**2           # |J_total|²
            # Integrate: area ≈ slot_area, mean of J²
            P_cu_ac += (1.0 / (2.0 * gp.sigma_cu)) * float(J2.mean()) * slot_area * gp.stack_length

        # DC analytical baseline for R_phase (used for winding summary)
        cu_dc = compute_copper_losses(**{k: li[k] for k in [
            "I_phase_rms", "n_coils_per_phase", "n_parallel", "n_series",
            "n_wires_per_slot", "wire_width_m", "wire_height_m",
            "motor_length_m", "r_slot_mid_m", "n_slots",
        ]})

        # ── Iron losses (Bertotti, from PINN |B| field) ────────────────────
        try:
            from motor_ai_sim import materials as mat_lib
            from motor_ai_sim.config import get_config as _gc
            mat_cfg = _gc().get("materials", {})
            steel   = mat_lib.get_material("steel", mat_cfg.get("stator_core", "20SW1200"))
            kh, kc, ke = steel.core_loss_kh, steel.core_loss_kc, steel.core_loss_ke
        except Exception:
            kh, kc, ke = 2.5, 0.003, 0.0

        fe = compute_iron_losses_by_domain(
            A_z_fn, frequency=li["frequency"],
            stack_length=gp.stack_length,
            r_stator_in=gp.r_stator_in, r_stator_out=gp.r_stator_out,
            r_rotor_in=gp.r_rotor_in,   r_rotor_out=gp.r_rotor_out,
            kh=kh, kc=kc, ke=ke,
        )

        # ── Magnet eddy losses — AC (from PINN A_z field) ─────────────────
        # P_mag = (ω²σ_mag/2) · stack_length · Σ_magnets ∫|A_z|² dA
        P_mag_ac = 0.0
        for _, mag_geo, _ in self.geo.magnet_domains():
            pts = mag_geo.sample_interior(n=1000)
            xs  = pts["x"].ravel()
            ys  = pts["y"].ravel()
            Ar_m, Ai_m = _eval(xs, ys)
            A2 = Ar_m**2 + Ai_m**2
            # Magnet sector area ≈ (π * (r_out²−r_in²) / num_poles) * fill
            sector_area = (math.pi * (gp.r_rotor_out**2 - gp.r_rotor_in**2)
                           / gp.num_poles * gp.magnet_fill_fraction)
            P_mag_ac += (omega**2 * gp.sigma_mag / 2.0) * float(A2.mean()) * sector_area * gp.stack_length

        # ── Efficiency ─────────────────────────────────────────────────────
        eff = compute_efficiency(
            torque_Nm=torque, rpm=li["rpm"],
            P_cu_W=P_cu_ac,
            P_fe_W=fe["P_fe_total_W"],
            P_mag_W=P_mag_ac,
        )

        log.info(
            "Post-process: T=%.3f Nm  P_cu_ac=%.1f W  P_fe=%.1f W  "
            "P_mag=%.1f W  eta=%.1f%%",
            torque, P_cu_ac, fe["P_fe_total_W"], P_mag_ac,
            eff["efficiency_pct"] or 0,
        )

        return {
            "torque_Nm":        torque,
            "B_max_T":          float(B_mag.max()),
            "B_mean_T":         float(B_mag.mean()),
            "training_steps":   self.cfg.max_steps,
            "output_dir":       str(self.cfg.output_dir),
            # Losses (AC — from PINN field)
            "P_cu_total_W":     P_cu_ac,
            "P_cu_dc_W":        cu_dc["P_cu_total_W"],   # DC baseline
            "R_phase_ohm":      cu_dc["R_phase_ohm"],
            "P_fe_stator_W":    fe["P_fe_stator_W"],
            "P_fe_rotor_W":     fe["P_fe_rotor_W"],
            "P_mag_eddy_W":     P_mag_ac,
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
        """Build a complete PhysicsNeMoConfig-compatible OmegaConf object.

        PhysicsNeMo's Solver+Trainer expects all the keys listed below; if
        any are missing it errors with "Missing key X". We populate the
        ones it reads with sensible defaults; the rest can stay empty.
        """
        cfg = self.cfg
        from omegaconf import OmegaConf
        net_dir = str(cfg.output_dir / "network")
        Path(net_dir).mkdir(parents=True, exist_ok=True)

        d = {
            # Top-level
            "network_dir":              net_dir,
            "initialization_network_dir": "",
            "save_filetypes":           "vtk,npz",
            "summary_histograms":       "off",
            "jit":                      False,           # avoid PyTorch JIT issues
            "jit_use_nvfuser":          False,
            "jit_arch_mode":            "only_activation",
            "jit_autograd_nodes":       False,
            "cuda_graphs":              False,          # disable for portability
            "cuda_graph_warmup":        20,
            "find_unused_parameters":   False,
            "broadcast_buffers":        False,
            "device":                   cfg.device,
            "debug":                    False,
            "run_mode":                 "train",

            # Training schedule
            "training": {
                "max_steps":            int(cfg.max_steps),
                "grad_agg_freq":        1,
                "rec_results_freq":     max(cfg.max_steps // 5, 100),
                "rec_validation_freq":  max(cfg.max_steps // 5, 100),
                "rec_inference_freq":   max(cfg.max_steps // 5, 100),
                "rec_monitor_freq":     max(cfg.max_steps // 5, 100),
                "rec_constraint_freq":  max(cfg.max_steps // 5, 100),
                "save_network_freq":    max(cfg.max_steps // 2, 200),
                "print_stats_freq":     max(cfg.max_steps // 10, 50),
                "summary_freq":         max(cfg.max_steps // 5, 100),
                "grad_clip_max_norm":   0.5,
                "monitor_grad_clip":    False,
                # Neural Tangent Kernel reweighting (off)
                "ntk": {"use_ntk": False, "save_name": "ntk", "run_freq": 1000},
            },

            # Early-stopping (disabled — train full max_steps)
            "stop_criterion": {
                "metric":    None,
                "min_delta": None,
                "patience":  100000,
                "mode":      "min",
                "freq":      1000,
                "strict":    False,
            },

            # Optimizer (Adam — instantiated via Hydra)
            "optimizer": {
                "_target_": "torch.optim.Adam",
                "_params_": {
                    # physicsnemo-sym 2.4 renamed the strategy methods:
                    # adam → adam_compute_gradients / adam_apply_gradients
                    "compute_gradients": "adam_compute_gradients",
                    "apply_gradients":   "adam_apply_gradients",
                },
                "lr":     cfg.learning_rate,
                "betas":  [0.9, 0.999],
                "eps":    1e-8,
                "weight_decay": 0.0,
            },

            # Scheduler — keep LR constant
            "scheduler": {
                "_target_": "torch.optim.lr_scheduler.ConstantLR",
                "factor":   1.0,
                "total_iters": int(cfg.max_steps),
            },

            # Loss aggregator (PhysicsNeMo's standard SumLoss)
            "loss": {
                "_target_": "physicsnemo.sym.loss.aggregator.Sum",
                "weights":  None,
            },

            # AMP / batch_size at top level (defaults from physicsnemo default config)
            "amp":        False,
            "amp_dtype":  "float16",
            "batch_size": {"interior": cfg.batch_size_interior, "boundary": cfg.batch_size_boundary},

            # Profiler
            "profiler": {
                "profile":    False,
                "start_step": 0,
                "end_step":   100,
            },

            "graph": {"func_arch": False, "func_arch_allow_partial_hessian": True},
            "custom": {},
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
