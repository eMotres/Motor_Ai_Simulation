"""The rotor's loss map is made periodic per pole before the thermal solve
(2026-09-09).

User, on the G2-L40 quarter after both cut-line films were fixed: *"опять та же
картина с пятнами, ничего не поменялось"*.  The remaining spots were the INPUT:
the Electromagnetic sector solve's per-element eddy loss differed from pole to
pole — the seven magnets of the quarter carried 4.14 / 4.30 / 3.85 / 3.98 / 4.08
/ 4.05 / 3.48 W (±5 %, the pole at the 90° cut 15 % short) and that pole was the
2 K cooler one.  A balanced machine heats every pole alike, so each rotor-side
domain's per-pole watts are brought to their mean, keeping the shape inside a
pole and the total.
"""
from __future__ import annotations

import math

import numpy as np

from motor_ai_sim.routes.thermal import _symmetrise_rotor_losses_per_pole


def _sector_mesh(theta0_deg: float, theta1_deg: float, r0: float, r1: float,
                 nr: int = 6, nt: int = 42):
    """A structured annular sector; returns verts (n,2), tris (m,3), centroid r."""
    rs = np.linspace(r0, r1, nr)
    ts = np.radians(np.linspace(theta0_deg, theta1_deg, nt))
    R, TH = np.meshgrid(rs, ts, indexing="ij")
    verts = np.stack([(R * np.cos(TH)).ravel(), (R * np.sin(TH)).ravel()], axis=1)
    tris = []
    for i in range(nr - 1):
        for j in range(nt - 1):
            a = i * nt + j; b = a + 1; c = a + nt; d = c + 1
            tris.append([a, b, d]); tris.append([a, d, c])
    tris = np.asarray(tris, int)
    return verts, tris


def _areas(verts, tris):
    p = verts[tris]
    return 0.5 * np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                        - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))


def _per_pole_watts(ld, verts, tris, mask, pitch_deg, a0):
    cen = verts[tris].mean(axis=1)
    ang = (np.degrees(np.arctan2(cen[:, 1], cen[:, 0])) - a0) % 360.0
    k = np.floor(ang / pitch_deg).astype(int)
    a = _areas(verts, tris)
    n = int(k[mask].max()) + 1
    return np.array([float((ld * a)[mask & (k == i)].sum()) for i in range(n)])


def test_uneven_poles_come_out_equal_and_the_total_is_kept():
    # a quarter with 7 poles: the rotor annulus 0.05..0.073 m, tag 5, and a
    # "stator" ring outside it, tag 1, which must be left alone
    verts, tris = _sector_mesh(0.0, 90.0, 0.050, 0.100, nr=11, nt=57)
    cen = verts[tris].mean(axis=1)
    rc = np.hypot(cen[:, 0], cen[:, 1])
    tags = np.where(rc < 0.0734, 5, 1)
    pitch = 90.0 / 7
    ang = np.degrees(np.arctan2(cen[:, 1], cen[:, 0]))
    pole = np.floor(ang / pitch).astype(int)
    # the finding, as a per-pole factor on a uniform 1e5 W/m³ rotor map
    factors = np.array([4.14, 4.30, 3.85, 3.98, 4.08, 4.05, 3.48]) / 4.0
    ld = np.full(len(tris), 1e5)
    ld[tags == 5] *= factors[np.clip(pole[tags == 5], 0, 6)]
    ld[tags == 1] = 3e5                                   # stator, uniform
    before = _per_pole_watts(ld, verts, tris, tags == 5, pitch, 0.0)
    assert before.max() / before.min() > 1.2, "the fixture has no spread"

    out, rep = _symmetrise_rotor_losses_per_pole(ld, verts, tris, tags, 0.0734, 4, 7)
    after = _per_pole_watts(out, verts, tris, tags == 5, pitch, 0.0)
    assert np.allclose(after, after.mean(), rtol=1e-9), after
    # total preserved, stator untouched
    a = _areas(verts, tris)
    assert math.isclose(float((out * a)[tags == 5].sum()), float((ld * a)[tags == 5].sum()), rel_tol=1e-12)
    assert np.array_equal(out[tags == 1], ld[tags == 1])
    # and the report says what was removed
    assert rep["5"]["poles"] == 7
    assert rep["5"]["min"] < 0.9 and rep["5"]["max"] > 1.05


def test_a_sector_straddling_minus_180_bins_correctly():
    verts, tris = _sector_mesh(135.0, 225.0, 0.050, 0.100, nr=7, nt=43)
    cen = verts[tris].mean(axis=1)
    rc = np.hypot(cen[:, 0], cen[:, 1])
    tags = np.where(rc < 0.0734, 5, 1)
    ld = np.full(len(tris), 1e5)
    # a hot half: elements in the first 45° of the sector at ×1.5
    ang = np.degrees(np.arctan2(cen[:, 1], cen[:, 0]))
    first_half = ((ang - 135.0) % 360.0) < 45.0
    ld[(tags == 5) & first_half] *= 1.5
    out, rep = _symmetrise_rotor_losses_per_pole(ld, verts, tris, tags, 0.0734, 4, 6)
    after = _per_pole_watts(out, verts, tris, tags == 5, 90.0 / 6, 135.0)
    assert np.allclose(after, after.mean(), rtol=1e-9), after
    assert rep["5"]["poles"] == 6


def test_nothing_happens_with_one_pole_or_no_rotor_loss():
    verts, tris = _sector_mesh(0.0, 90.0, 0.050, 0.100, nr=5, nt=21)
    tags = np.full(len(tris), 5)
    ld = np.random.default_rng(0).uniform(1e4, 1e5, len(tris))
    out, rep = _symmetrise_rotor_losses_per_pole(ld, verts, tris, tags, 0.0734, 4, 1)
    assert np.array_equal(out, ld) and rep == {}
    out, rep = _symmetrise_rotor_losses_per_pole(np.zeros(len(tris)), verts, tris, tags, 0.0734, 4, 7)
    assert not out.any() and rep == {}
