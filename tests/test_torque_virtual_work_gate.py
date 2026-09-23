"""No-CAD frozen-current virtual-work check on an explicit torn annulus.

Standalone: python tests/test_torque_virtual_work_gate.py (project solver Python).
No test-suite conftest, saved configuration, CAD, gmsh, or live API is used.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from motor_ai_sim.simulation.static3d.band import BandedSection
from motor_ai_sim.simulation.static3d.band2d import Banded2D
from motor_ai_sim.simulation.static3d.motor_geometry import MotorSection, RegionSpec
from motor_ai_sim.simulation.static3d.motor_mesh import Section2D


class FixedWinding:
    """Stationary radial T field; its amplitude is held fixed at all shifts."""
    def __init__(self, sign=1.0, phase=0.0):
        self.sign = sign
        self.phase = phase

    def psi_at(self, r, th):
        return np.where(np.asarray(r) >= .002,
                        self.sign * 120000.0 * np.sin(2 * th + self.phase), 0.0)


def annulus(n_sector_cells: int, current_sign: float = 1.0,
            source_phase: float = 0.0, nonlinear: bool = False):
    pitch = math.pi / (2 * n_sector_cells)
    angles = np.arange(n_sector_cells + 1) * pitch
    nodes = []
    strips = []
    for radii in ((1.0, 2.0), (2.0, 3.0)):
        start = len(nodes)
        for radius in radii:
            nodes.extend((radius * math.cos(a), radius * math.sin(a)) for a in angles)
        strips.append((np.arange(start, start + n_sector_cells + 1),
                       np.arange(start + n_sector_cells + 1,
                                 start + 2 * (n_sector_cells + 1))))
    triangles, tags = [], []
    for piece, (inside, outside) in enumerate(strips):
        for j in range(n_sector_cells):
            a, b, c, d = inside[j], outside[j], inside[j+1], outside[j+1]
            triangles.extend(((a,b,c), (b,d,c)))
            # Off-axis rotor reluctance patch; its interface moves at each weld.
            tag = 1 if piece == 0 and j < n_sector_cells // 3 else 0
            tags.extend((tag, tag))
    p = np.asarray(nodes, float).T
    t = np.asarray(triangles, np.int64).T
    masters = np.asarray([row[0] for strip in strips for row in strip])
    slaves = np.asarray([row[-1] for strip in strips for row in strip])
    sect = Section2D(p=p, t=t, tri_region=np.asarray(tags), names={"rotor_iron": 1},
                     masters=masters, slaves=slaves, r_box_mm=3.0)
    curve = [(0., 0.), (3000., .02), (9000., .05),
             (25000., .10), (100000., .20)] if nonlinear else None
    section = MotorSection(geo={}, regions=[RegionSpec("rotor_iron", None,
                              mu_r=6, bh_curve=curve)],
                           n_sectors=4, sector_deg=90, antiperiodic=True,
                           r_stator_out_mm=3, r_stator_in_mm=2,
                           r_rotor_out_mm=2, r_rotor_in_mm=1,
                           r_shaft_in_mm=0, mid_r_mm=2, stack_mm=10,
                           num_slots=4, num_poles=4, pole_pairs=2,
                           Br_T=0, mu_rec=1)
    banded = BandedSection(sect=sect, n_ring=4*n_sector_cells,
                           rring=strips[0][1], sring=strips[1][0],
                           n_rotor_nodes=2*(n_sector_cells+1), r_mid_mm=2,
                           sector_rad=math.pi/2, bc_sign=-1)
    return Banded2D(section, banded, element_order=2, linear_iron=not nonlinear,
                    magnets_off=True, winding=FixedWinding(current_sign, source_phase),
                    nu_pointwise=True)


class VirtualWorkGate(unittest.TestCase):
    def test_real_p2_weld_energy_and_nonzero_reluctance_torque(self):
        results = []
        for n in (12, 24):
            model = annulus(n)
            values = {}
            for shift in (-2, -1, 0, 1, 2):
                solution = model.solve(shift)
                self.assertTrue(solution.picard["converged"])
                self.assertEqual(solution.nu_el.ndim, 2)  # pointwise P2 path
                energy = model.co_energy(solution)
                load_energy = model.co_energy_from_load(solution)
                self.assertAlmostEqual(energy, load_energy, delta=abs(energy)*2e-9)
                values[shift] = energy
            pitch = model.pitch_rad
            t1 = (values[1] - values[-1]) / (2*pitch)
            t2 = (values[2] - values[-2]) / (4*pitch)
            self.assertGreater(abs(t1), 1e-6)
            self.assertEqual(np.sign(t1), np.sign(t2))
            results.append((t1, t2))
        # Angular step halving should reduce the central-difference spread.
        self.assertLess(abs(results[1][0]-results[1][1]),
                        abs(results[0][0]-results[0][1]))
        reverse = annulus(12, current_sign=-1)
        w_minus = reverse.co_energy(reverse.solve(-1))
        w_plus = reverse.co_energy(reverse.solve(1))
        reverse_torque = (w_plus - w_minus) / (2 * reverse.pitch_rad)
        # Pure reluctance torque is even under reversal of all currents.
        self.assertAlmostEqual(reverse_torque, results[0][0],
                               delta=abs(results[0][0])*1e-10)
        # Independent perturbation: advance the prescribed source's phase
        # (equivalent to rotating the winding backward) while keeping the
        # weld fixed. It gives the same relative rotor/winding displacement.
        source_checks = []
        for n, (weld_torque, _) in zip((12, 24), results):
            delta = math.pi / (2*n)
            plus = annulus(n, source_phase=2*delta)
            minus = annulus(n, source_phase=-2*delta)
            w_plus = plus.co_energy(plus.solve(0))
            w_minus = minus.co_energy(minus.solve(0))
            source_torque = (w_plus-w_minus)/(2*delta)
            self.assertAlmostEqual(source_torque, weld_torque,
                                   delta=abs(weld_torque)*1e-7)
            source_checks.append((weld_torque, source_torque))
        print("weld/source rotation (N m):", source_checks)
        print("coenergy torque (N m), coarse/fine:", results)

    def test_nonlinear_fixed_current_source_work_identity(self):
        n = 12
        model = annulus(n, nonlinear=True)
        delta = model.pitch_rad
        w = {}
        for shift in (-1, 0, 1):
            sol = model.solve(shift, tol=1e-6, max_iter=100)
            self.assertTrue(sol.picard["converged"], sol.picard)
            self.assertLess(sol.picard["history"][-1], 1e-6)
            w[shift] = model.co_energy(sol)
        weld_torque = (w[1]-w[-1])/(2*delta)
        self.assertGreater(abs(weld_torque), 1e-7)
        # Exact source-phase derivative at the unshifted solved field. The
        # winding phase is 2*mechanical angle, hence the factor two.
        xy = np.asarray(model.basis.global_coordinates())
        radius = np.hypot(xy[0], xy[1])
        angle = np.arctan2(xy[1], xy[0])
        d_amp = np.where(radius >= .002, 120000.0*np.cos(2*angle), 0.0)
        dT = np.stack((d_amp*xy[0]/radius, d_amp*xy[1]/radius))
        centre = model.solve(0, tol=1e-6, max_iter=100)
        source_work = 2 * model.volume_factor * float(centre.A @ model._source_load(dT))
        # Compare against phase perturbations with the weld fixed; this
        # isolates the coenergy/source conjugacy from rotor re-labelling.
        phase_h = 1e-3
        phase_w = []
        for phase in (-phase_h, phase_h):
            m = annulus(n, source_phase=phase, nonlinear=True)
            s = m.solve(0, tol=1e-6, max_iter=100)
            self.assertTrue(s.picard["converged"], s.picard)
            phase_w.append(m.co_energy(s))
        phase_difference = 2*(phase_w[1]-phase_w[0])/(2*phase_h)
        matching_phase_w = []
        for phase in (-2*delta, 2*delta):
            m = annulus(n, source_phase=phase, nonlinear=True)
            s = m.solve(0, tol=1e-6, max_iter=100)
            self.assertTrue(s.picard["converged"], s.picard)
            matching_phase_w.append(m.co_energy(s))
        matched_source_difference = (matching_phase_w[1]-matching_phase_w[0])/(2*delta)
        print("nonlinear weld/source-work/source-difference (N m):",
              weld_torque, source_work, phase_difference, matched_source_difference)
        self.assertAlmostEqual(source_work, phase_difference,
                               delta=abs(source_work)*1e-4)
        self.assertAlmostEqual(weld_torque, matched_source_difference,
                               delta=abs(weld_torque)*1e-7)


if __name__ == "__main__":
    unittest.main()
