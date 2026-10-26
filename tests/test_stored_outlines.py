"""The part outlines of a rotor map are the DOMAIN BOUNDARIES of its mesh (owner screen 2026-10-26; synthetic data only).

The stored mechanical map drew a zigzag of straight V lines from pole tip to pole tip down to the hub.  Cause: a sector answer
is replicated by copying the sector WITH ITS OWN NODES, so every seam between two copies is an edge with one triangle on each
side that no index connects - it read as a border - and the polygon outlines of the clipped sector carry the two radial cut
faces too.  Pinned here against a synthetic SPOKE rotor (iron ring, magnet slabs between pole wedges, sleeve, hub) built twice:

  * as ONE welded mesh of the whole rotor (the ground truth: its domain boundaries are the rotor's edges), and
  * as ONE SECTOR replicated n times with duplicated seam nodes (what the solver hands out),

and `domain_boundary_loops` must give the same segment set for both - no seam, no cut face, every part edge once.  A PNG of
both is written (``MOTRES_TEST_ARTIFACTS`` or the test's tmp folder) for a look.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.mechanical.rotor_stress import _replicate_field, domain_boundary_loops

N_SECTORS = 8
NTH = 12                                   # angular cells per sector
RADII = [10.0, 20.0, 24.0, 28.0, 32.0, 34.0, 36.0]
THETA0 = 0.3                               # the sector does not start on an axis
MAGNET_CELLS = range(4, 8)                 # the middle third of a sector: cuts fall in the iron
IRON, MAGNET, SLEEVE, SHAFT = 0, 1, 2, 3


def _domain(r_mid: float, jj: int) -> int:
    if r_mid < 20.0:
        return SHAFT
    if r_mid >= 34.0:
        return SLEEVE
    if 24.0 <= r_mid < 32.0 and jj in MAGNET_CELLS:
        return MAGNET
    return IRON


def _grid(n_cells: int, theta0: float, wrap: bool, domain_of_cell):
    """Polar grid: nodes (ring i, angle j); `wrap` shares the last angle column with the first (a full ring)."""
    nr, d_th = len(RADII), 2 * math.pi / N_SECTORS / NTH
    cols = n_cells if wrap else n_cells + 1
    verts = [(RADII[i] * math.cos(theta0 + j * d_th), RADII[i] * math.sin(theta0 + j * d_th)) for i in range(nr) for j in range(cols)]
    node = lambda i, j: i * cols + (j % cols if wrap else j)       # noqa: E731
    tris, dom = [], []
    for i in range(nr - 1):
        for j in range(n_cells):
            a, b, c, d = node(i, j), node(i, j + 1), node(i + 1, j + 1), node(i + 1, j)
            r_mid = 0.5 * (RADII[i] + RADII[i + 1])
            dd = domain_of_cell(r_mid, j)
            tris += [[a, b, c], [a, c, d]]
            dom += [dd, dd]
    return np.array(verts), np.array(tris), np.array(dom)


def _segments(loops, q: float = 1e-2):
    out = set()
    for loop in loops:
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            pa, pb = (round(a[0] / q), round(a[1] / q)), (round(b[0] / q), round(b[1] / q))
            if pa != pb:
                out.add((min(pa, pb), max(pa, pb)))
    return out


@pytest.fixture(scope="module")
def rotors():
    # ground truth: the whole rotor as ONE mesh with shared nodes
    fv, ft, fd = _grid(N_SECTORS * NTH, THETA0, True, lambda r, j: _domain(r, j % NTH))
    truth = domain_boundary_loops(fv, ft, fd)
    # what the solver hands out: one sector, replicated with its own nodes per copy
    sv, st, sd = _grid(NTH, THETA0, False, _domain)
    sector = {"vertices": sv.tolist(), "triangles": st.tolist(), "domain_per_tri": sd.tolist(),
              # the polygon outlines of the clipped sector: the part rings INCLUDING the two radial cut faces
              "outlines": [[[sv[0][0], sv[0][1]], [sv[-1][0], sv[-1][1]]]], "cases": {}, "contact_segments_per_pair": {}}
    rep = _replicate_field(sector, N_SECTORS)
    return {"truth": truth, "replicated": rep, "full": (fv, ft, fd)}


def test_the_replicated_sector_has_the_outlines_of_the_whole_rotor(rotors):
    truth = _segments(rotors["truth"])
    got = _segments(rotors["replicated"]["outlines"])
    assert got == truth, (len(got), len(truth), sorted(got - truth)[:3], sorted(truth - got)[:3])
    assert len(truth) > 100                                   # a real outline, not an empty set equal to an empty set


def test_no_segment_lies_on_a_sector_cut(rotors):
    """The seam between two copies is a ray from the rim to the hub: inside the iron it is NOT an outline."""
    d_sector = 2 * math.pi / N_SECTORS
    for a, b in ((s[0], s[1]) for s in _segments(rotors["replicated"]["outlines"], 1e-2)):
        (x0, y0), (x1, y1) = ((a[0] * 1e-2, a[1] * 1e-2), (b[0] * 1e-2, b[1] * 1e-2))
        r0, r1 = math.hypot(x0, y0), math.hypot(x1, y1)
        radial = abs(r1 - r0) > 0.5 and abs(((math.atan2(y1, x1) - math.atan2(y0, x0) + math.pi) % (2 * math.pi)) - math.pi) < 1e-2
        if not radial:
            continue
        # a radial segment may only be a magnet side (r 24..32 at a magnet edge angle), never an iron-only cut
        phase = ((math.atan2(y0, x0) - THETA0) % d_sector) / (d_sector / NTH)
        assert 3.99 < phase < 4.01 or 7.99 < phase < 8.01, (a, b, phase)


def test_the_old_index_based_reading_draws_the_seams(rotors):
    """The bug itself: by index the replicated sectors are separate bodies, so every seam edge is a 'border'."""
    rep = rotors["replicated"]
    t, d = np.asarray(rep["triangles"]), np.asarray(rep["domain_per_tri"])
    v = np.asarray(rep["vertices"])
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e.sort(axis=1)
    uniq, counts = np.unique(e, axis=0, return_counts=True)
    single = uniq[counts == 1]
    r = np.hypot(v[:, 0], v[:, 1])
    radial = [(int(a), int(b)) for a, b in single if abs(r[a] - r[b]) > 1.0]
    # every magnet side is shared with iron inside one sector, so a single-sided RADIAL edge can only be a seam
    assert len(radial) >= 2 * N_SECTORS * 4


def test_picture_of_the_spoke_rotor(rotors, tmp_path):
    plt = pytest.importorskip("matplotlib")
    plt.use("Agg")
    import matplotlib.pyplot as pl
    out_dir = Path(os.environ.get("MOTRES_TEST_ARTIFACTS") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = pl.subplots(1, 2, figsize=(10, 5))
    for ax, loops, title in ((axes[0], rotors["truth"], "whole rotor, one welded mesh"),
                             (axes[1], rotors["replicated"]["outlines"], "sector replicated (stored map)")):
        for lp in loops:
            ax.plot([lp[0][0], lp[1][0]], [lp[0][1], lp[1][1]], "k-", lw=0.6)
        ax.set_aspect("equal")
        ax.set_title(title)
    path = out_dir / "stored_outlines_spoke_rotor.png"
    fig.savefig(path, dpi=110)
    pl.close(fig)
    assert path.stat().st_size > 5000


def test_a_full_rotor_with_split_interface_nodes_draws_each_part_edge_once():
    """The mechanical mesh is split: the nodes of an interface are duplicated, one set per part.  One line, not two or none."""
    v = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [1, 0], [2, 0], [2, 1], [1, 1]], float)    # right part has its own x=1 nodes
    t = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    loops = domain_boundary_loops(v, t, [0, 0, 1, 1])
    seg = _segments(loops, 1e-3)
    interface = ((1000, 0), (1000, 1000))
    assert interface in seg and len(seg) == 7                  # 6 border edges + the interface once
