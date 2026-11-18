"""Sector-cut node pairing on a SMALL machine (BUG_sweep_magnet_flat.md item 3).

The cut-line selector took every node within an absolute 10 um of the cut.  On the 12 mm die the belt node next to the cut at
r = 3.408 mm sits 9.9 um off the line (0.17 deg), so it was taken for a cut node and the half-sector solve stopped with
"sector cut edges do not match across the cut".  The tolerance is now bounded by the machine size (2e-4 * r_max); for
r_max >= 50 mm it is the 10 um it always was."""
from types import SimpleNamespace

import numpy as np

from motor_ai_sim.simulation.mesher import _pair_sector_cut_nodes


def _mesh(r_list, extra_near_cut_um=None, r_outer=None):
    """nodes on the +X cut (theta 0) and on the 180-degree cut (n_sectors 2), radii in metres, plus optionally one node
    on EACH cut line shifted off the line by ``extra_near_cut_um`` (the neighbouring belt node)."""
    pts = [(r, 0.0) for r in r_list] + [(-r, 0.0) for r in r_list]
    if extra_near_cut_um is not None:
        d = extra_near_cut_um * 1e-6
        pts += [(r_list[1], d), (-r_list[1], -d)]
    if r_outer:
        pts += [(r_outer, 0.0), (-r_outer, 0.0)]
    return SimpleNamespace(p=np.array(pts, float).T)


def test_a_small_machine_does_not_take_the_neighbouring_belt_node_for_a_cut_node():
    r = [3.358e-3, 3.408e-3, 3.458e-3, 5.0e-3, 6.0e-3]
    m = _mesh(r, extra_near_cut_um=9.9)
    ms, ss = _pair_sector_cut_nodes(m, 2)
    assert len(ms) == len(ss) == len(r)
    pts = m.p
    assert np.all(np.abs(pts[1, ms]) < 1e-9) and np.all(np.abs(pts[1, ss]) < 1e-9)      # only nodes ON the lines
    assert np.allclose(np.hypot(*pts[:, ms]), np.hypot(*pts[:, ss]))


def test_a_large_machine_keeps_the_ten_micrometre_tolerance():
    r = [0.05, 0.06, 0.07, 0.1]
    m = _mesh(r, extra_near_cut_um=9.0)
    ms, ss = _pair_sector_cut_nodes(m, 2)
    assert len(ms) == len(r) + 1                      # the 9 um node is still a cut node, as before the change
    m0 = _mesh(r)
    ms0, ss0 = _pair_sector_cut_nodes(m0, 2)
    assert list(ms0) == list(range(len(r))) and list(ss0) == [len(r) + i for i in range(len(r))]


def test_nodes_exactly_on_the_lines_pair_in_radius_order_at_any_size():
    for scale in (1e-3, 1e-2, 1e-1):
        r = [k * scale for k in (1.0, 1.5, 2.0, 3.0)]
        ms, ss = _pair_sector_cut_nodes(_mesh(r), 2)
        assert list(ms) == [0, 1, 2, 3] and list(ss) == [4, 5, 6, 7]
