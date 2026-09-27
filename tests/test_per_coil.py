"""Per-coil excitation (six-coil study): algebra only, no FEM solve."""
import math

import numpy as np
import pytest

from motor_ai_sim.simulation.drive import Excitation
from motor_ai_sim.simulation.geometry_2d import build_winding_layout
from motor_ai_sim.simulation import per_coil as pc

PP, DAX, GAM = 5, 120.0138, 15.0


def _coils():
    return pc.coil_map(build_winding_layout(12, PP))


def test_l155_coils_sit_at_60_deg_el():
    cs = _coils()
    assert [(c.phase, c.sign) for c in cs] == [
        ("A", 1), ("B", -1), ("C", 1), ("A", -1), ("B", 1), ("C", -1)]
    assert [c.phi_deg for c in cs] == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]


def test_pure_sine_reproduces_the_stock_current_drive():
    ipk = 229.46
    exc = Excitation(pole_pairs=PP, daxis_deg=DAX, i_peak=ipk, gamma_deg=GAM)
    src = pc.PerCoilCurrentSource(_coils(), pc.CoilWaveform(ipk), pole_pairs=PP,
                                  daxis_deg=DAX, gamma_deg=GAM)
    for th in np.linspace(0.0, 72.0, 37):
        a, b = exc.currents(th), src.currents(th)
        for k in "ABC":
            assert b[k] == pytest.approx(a[k], abs=1e-9)


def test_third_harmonic_is_zero_sequence_and_pairs():
    w = pc.CoilWaveform(100.0, ((3, 0.1, 30.0),))
    src = pc.PerCoilCurrentSource(_coils(), w, pole_pairs=PP, daxis_deg=DAX,
                                  gamma_deg=GAM)
    s = [sum(src.currents(th).values()) for th in np.linspace(0, 72, 50)]
    assert max(abs(x) for x in s) > 10.0          # not balanced: triplen flows


def test_even_harmonic_refused():
    with pytest.raises(pc.PairingError):
        pc.PerCoilCurrentSource(_coils(), pc.CoilWaveform(100.0, ((2, 0.1, 0.0),)),
                                pole_pairs=PP, daxis_deg=0.0, gamma_deg=0.0)


def test_equal_rms_normalisation():
    w = pc.CoilWaveform(0.0, ((3, 0.2, 0.0), (5, 0.05, 10.0))).scaled_to_rms(162.3)
    src = pc.PerCoilCurrentSource(_coils(), w, pole_pairs=PP, daxis_deg=0.0,
                                  gamma_deg=0.0)
    for v in pc.coil_rms(src).values():
        assert v == pytest.approx(162.3, rel=1e-6)


def test_open_coil_layout_and_source():
    orig = build_winding_layout(12, PP)
    with pc.open_coils_layout([0]):
        from motor_ai_sim.simulation import geometry_2d as g2
        lay = g2.build_winding_layout(12, PP)
    assert lay[0] == ("A", 0) and lay[1] == ("A", 0) and lay[2:] == orig[2:]
    assert build_winding_layout(12, PP) == orig             # restored
    src = pc.PerCoilCurrentSource(_coils(), pc.CoilWaveform(100.0), pole_pairs=PP,
                                  daxis_deg=0.0, gamma_deg=0.0,
                                  coil_scale=[0, 1, 1, 1, 1, 1])
    ic = src.coil_currents(3.0)
    assert ic[0] == 0.0
    assert src.currents(3.0)["A"] == pytest.approx(-ic[3])  # partner still drives A


def test_gap_forces_uniform_field_has_no_net_force():
    # a ring of air triangles between r=1.0 and 1.1 (stator side) + iron outside
    n = 360
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    r0, r1, r2 = 1.0, 1.1, 1.2
    P = np.concatenate([np.vstack([r * np.cos(th), r * np.sin(th)]) for r in (r0, r1, r2)],
                       axis=1)
    T, tags = [], []
    for ring, tag in ((0, 3), (1, 1)):
        for i in range(n):
            a, b = ring * n + i, ring * n + (i + 1) % n
            c, d = a + n, b + n
            T += [[a, b, c], [b, d, c]]; tags += [tag, tag]
    T = np.array(T).T
    # radial field B_r = cos(p th): symmetric, zero net force; add a 1-pole
    # shift (p and p+1) and a net force must appear
    cx = P[0][T].mean(axis=0); cy = P[1][T].mean(axis=0); ang = np.arctan2(cy, cx)

    def frame(br):
        return {"P_mm": P * 1e3, "Bx": br * np.cos(ang), "By": br * np.sin(ang),
                "step_idx": 0}
    base = np.cos(5 * ang)
    res = {"frames": [frame(base)], "frames_mesh": {"T": T, "tags": np.array(tags),
                                                   "nsn": P.shape[1]}}
    g = pc.gap_forces(res, 1.0)
    assert g["F_mag_max_N"] < 1e-6 * (1.0 / pc.MU0)
    res["frames"] = [frame(base + 0.2 * np.cos(4 * ang))]
    g2 = pc.gap_forces(res, 1.0)
    assert g2["F_mag_max_N"] > 1e3
