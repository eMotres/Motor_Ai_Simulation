"""The sector-cut edges beside the slip circle are welded even when the radius walk visits the two copies in the wrong
order (audit 2026-11-16, CIANO28 80_12 / L12-F475: the rotor-side Coulomb ring read +0.04 N.m)."""
import numpy as np

from motor_ai_sim.simulation.p2_projection import SlipProjection


def _proj(master_order, slave_order, drop=()):
    # master cut nodes 0..3 (r: rotor metal, rotor slip copy, stator slip copy, stator metal), slave cut nodes 10..13,
    # two ring nodes per half for the (trivial) slip ring
    edges = [(0, 1), (2, 3), (10, 11), (12, 13), (20, 21), (22, 23)]
    edges = [e for e in edges if e not in drop]
    facets = np.array(edges).T
    n_nodes = 24
    vdof = np.arange(n_nodes)
    fdof = np.arange(len(edges)) + n_nodes
    return SlipProjection(n_dof=n_nodes + len(edges), facets=facets, vdof=vdof, fdof=fdof,
                          rring=np.array([20, 21]), sring=np.array([22, 23]), nsn=0, n_ring=2, full_ring=False,
                          bc_sign=-1.0, Mn=np.array(master_order), Sn=np.array(slave_order),
                          dirichlet_dofs=np.array([], int))


def test_walk_in_mesh_order_welds_both_cut_edges_without_rescue():
    p = _proj([0, 1, 2, 3], [10, 11, 12, 13])
    assert p.n_cut_e == 2 and p.n_cut_e_rescued == 0 and p.n_cut_e_unmatched == 0


def test_walk_that_swaps_the_slip_copies_still_welds_both_cut_edges():
    p = _proj([0, 2, 1, 3], [10, 12, 11, 13])          # the walk steps rotor metal -> STATOR copy
    assert p.n_cut_e == 2 and p.n_cut_e_rescued == 2 and p.n_cut_e_unmatched == 0
    assert sorted((e[1], e[0]) for e in p._cut_e) == [(24 + 0, 24 + 2), (24 + 1, 24 + 3)]


def test_cut_edge_without_a_partner_is_counted():
    p = _proj([0, 1, 2, 3], [10, 11, 12, 13], drop=((10, 11),))       # the slave cut has no edge 10-11
    assert p.n_cut_e == 1 and p.n_cut_e_unmatched == 1
