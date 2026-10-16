"""The worst demagnetised element is a flagged CORNER diagnostic (2026-09-26).

It is a sharp-corner singularity that does not converge with the magnet mesh
(docs/NO_FILTERS_2026-09-24.md §3), so the solver ships it with its location
and flag under ``demag_summary["br_corner"]`` — never as the magnet's figure,
which is ``br_kept_vol_pct``.
"""

import math
from types import SimpleNamespace

import numpy as np

from motor_ai_sim.simulation.fem_solver_2d import (BR_CORNER_NOTE,
                                                   _demag_corner_diag)


def _mesh():
    # four triangles in metres; element 3 is the worst magnet element
    p = np.array([[0.000, 0.010, 0.000, 0.010, 0.020, 0.020],
                  [0.000, 0.000, 0.010, 0.010, 0.000, 0.010]])
    t = np.array([[0, 1, 1, 3],
                  [1, 3, 4, 4],
                  [2, 2, 3, 5]])
    return SimpleNamespace(p=p, t=t)


def test_the_corner_carries_value_location_magnet_and_flag():
    mesh = _mesh()
    mag_idx = np.array([1, 2, 3])
    brm = np.array([0.99, 0.97, 0.117])
    ar = np.array([5e-5, 5e-5, 5e-5])
    mags = [{"tag": 101, "idx": np.array([1])},
            {"tag": 102, "idx": np.array([2, 3])}]
    c = _demag_corner_diag(mesh, mag_idx, brm, ar, mags)
    assert c["flag"] == "corner" and c["note"] == BR_CORNER_NOTE
    assert c["br_pct"] == 11.7
    assert c["element"] == 3 and c["magnet_tag"] == 102
    x, y = 1e3 * (0.010 + 0.020 + 0.020) / 3, 1e3 * (0.010 + 0.000 + 0.010) / 3
    assert (c["x_mm"], c["y_mm"]) == (round(x, 3), round(y, 3))
    assert c["r_mm"] == round(math.hypot(x, y), 3)
    assert c["theta_deg"] == round(math.degrees(math.atan2(y, x)), 2)
    assert c["element_area_pct"] == round(100.0 / 3.0, 4)
    assert "not the magnet's figure" in c["note"]


def test_an_element_in_no_listed_magnet_has_no_tag():
    c = _demag_corner_diag(_mesh(), np.array([0, 1]), np.array([0.5, 0.9]),
                           np.array([1.0, 1.0]), [])
    assert c["magnet_tag"] is None and c["element"] == 0
