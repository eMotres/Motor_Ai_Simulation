"""Sliding-band domain tags and mesh tuning flags — the layer both the mesher
and the solver stand on.

Split out of ``fem_solver_2d`` so ``mesher`` can use these without importing the
solver and the solver can use them without importing the mesher. Nothing here
depends on either; it is deliberately the bottom of the dependency graph.

Every name is re-exported from ``fem_solver_2d`` for backwards compatibility —
``routes.simulation`` and ``modules.mesh`` reach for ``fs.DOM_MAG_BASE`` and
friends, and that has to keep working.
"""
from __future__ import annotations

import os as _os_sb
import threading

# Global lock around gmsh usage.  gmsh exposes a single C library
# instance per process, so two threads cannot concurrently call
# initialize()/model.add()/mesh.generate()/finalize() — the second
# call sees "Gmsh has not been initialized" if it lands between the
# first thread's init and finalize.  FastAPI runs sync 'def' endpoints
# (fem_field2d, fem_transient) on the threadpool, and the browser
# fires 2-4 of them in parallel on Simulation-tab mount, so without
# a lock the second request crashes the gmsh state.
_GMSH_LOCK = threading.RLock()

# Number of equally-spaced nodes on the sliding-band slip circle (r = mid_r).
# Shared by in_band (exterior) and out_band (hole) so the two half-meshes get
# IDENTICAL matching nodes there.  This count drives the angular resolution of
# the rotor-rotation merge, so lowering it raises torque ripple — keep it high.
# The VISUAL band width is controlled by the radial air-gap size field, not this.
#
# NB: the TRANSIENT solver does NOT use this fixed value — it computes an
# adaptive n_slip_eff = pole_pairs·per_period (per_period a multiple of 24, ≥120)
# so the slip ring is divisible by the pole-pair count for ANY motor and the
# electrical period tiles EXACTLY.  1008 = 14·72 tiled cleanly only for 14
# pole-pairs (28 poles); a 10-pp / 20-pole motor got 1008/10 = 100.8 → a coarse,
# 24-skipping step grid that under-resolved ripple & efficiency.  This constant
# stays the fallback for the static/eddy paths (_simplify_polys default) that
# don't know pole_pairs.
_N_SLIP = 1008

# Structured slip strips (explicit offset rings at mid±δ).  Tested as a fix for
# the order-6 sliding artifact: strips came out perfectly regular, but ord6 was
# UNCHANGED (1.94 vs 1.79) and the per-frame local stress balance got WORSE
# (frame-0 total −4.2 vs −0.1 N·m) → OFF by default; kept for experiments.
_SB_STRUCTURED_STRIPS = False

# Moving-band strip half-width as a fraction of the FULL gap (δ = frac·gap).
# 0.25 → strip spans half the gap (one closed-form row).  Smaller → thinner
# strip + more free rows per side (better radial resolution of the gap).
_SB_BAND_DELTA_FRAC = 0.25

# Rotational curve-mesh periodicity (pole->pole / slot->slot setPeriodic) in
# the SB half-builds — diagnostic gate.
_SB_ROT_PERIODICITY = True

_SB_POLE_COPY_ROTOR  = _os_sb.environ.get("SB_POLE_COPY_ROTOR",  "0") == "1"
_SB_POLE_COPY_STATOR = _os_sb.environ.get("SB_POLE_COPY_STATOR", "0") == "1"

# Uniform tangential RESAMPLING of the iron's gap-facing arcs (rotor OD, stator
# bore) on the slip angular grid — WITHOUT touching the slot-mouth / pocket
# corner angles (run endpoints are preserved exactly; only the chord sampling
# BETWEEN them is made uniform).  Attacks the free mesh's random chord
# "roughness" of the gap surfaces (sagitta noise ~µm modulating the local gap
# width), which projects into the ALLOWED 6k torque orders and is the reason
# h6/h12 amplitudes do not converge with mesh refinement.  Unlike the
# structured-gap snap this does NOT re-geometrise the mouths, so the cogging
# is not distorted.
# DEFAULT ON (2026-07-13, opt out with SB_IRON_RESAMPLE=0): besides the noise
# win (forbidden orders -> 0 in the A/B), unsnapped arcs are a HANG risk — the
# air-gap annulus and the iron discretise the same circle with different
# vertex counts, fragment leaves µm-thin sliver wedges on the bore, and gmsh's
# edge recovery splits those slivers forever (generate() never returns; took
# down every sweep eval on the 24s20p config).  Snapping both arcs to the slip
# grid removes the slivers by construction.
_SB_IRON_RESAMPLE = _os_sb.environ.get("SB_IRON_RESAMPLE", "1") == "1"
# Deterministic template iron mesh (iron_template.py): the WHOLE stator and
# rotor halves come from warped tensor grids instead of gmsh — rebuilds are
# bit-identical, so the raw-ripple rebuild band collapses.  Experimental
# (env SB_IRON_TEMPLATE=1); full-ring path first.
_SB_IRON_TEMPLATE = _os_sb.environ.get("SB_IRON_TEMPLATE", "0") == "1"
# Geometry-driven mesh (geo_mesh.py): triangulate the REAL CadQuery polygons
# (all fillets) with a constrained Delaunay instead of a warped tensor template
# — same DOM_* halves, same belt weld, but the mesh conforms to every fillet by
# construction (magnet corners, tooth-tip r1, V-notch apex).  Rides the SAME
# iron_template toggle + structured-gap belt; env SB_GEO_MESH=1 selects it over
# the tensor mesher.  Full ring first; sectors clone one wedge.
_SB_GEO_MESH = _os_sb.environ.get("SB_GEO_MESH", "0") == "1"
# Geometry-driven 1/N SECTOR wedge (geo_mesh sector meshers): the mesh + belt +
# anti-periodic solve all work and the MEAN physics matches the full ring.
# With the slot/pole CELL-TILED geo iron (geo_mesh._tile_cells) the sector is a
# bit-exact subset of the full ring — measured sector==full at two mesh
# densities (24.6/25.3% and 23.6% @ x2), spectra identical, only 6k orders.
# ON by default; SB_GEO_SECTOR=0 reverts a geo 1/N request to the full ring.
_SB_GEO_SECTOR = _os_sb.environ.get("SB_GEO_SECTOR", "1") != "0"
# Structured (concentric-ring) air gap: partition EACH half-gap (rotor OD->R1 and
# R2->stator bore) into `gap_layers` thin annular rows bounded by uniform N-gon rings
# on the slip angular grid -> the gap meshes as an ANSYS-style structured band
# (concentric circles + near-radial spokes) instead of free Delaunay triangles.  The
# slip band R1..R2 is untouched.  EXPERIMENTAL (default OFF, env SB_STRUCTURED_GAP=1):
# the dominant torque-ripple source is the tooth/slot half-mesh discretisation, NOT the
# gap coupling (see _SB_AIRGAP_MACRO / #141), so this mainly regularises the gap mesh
# and the slip re-pairing noise -- MEASURE before trusting it to move ripple.
_SB_STRUCTURED_GAP = _os_sb.environ.get("SB_STRUCTURED_GAP", "0") == "1"
# BELT: the DEFAULT implementation behind the structured_gap toggle.  The gap
# annulus (r_ro→mid→r_si) is built directly in numpy — K uniform rings × the
# n_slip angular grid per half — and WELDED into the gmsh half-mesh by exact
# node identity: the iron and the pocket/mouth air are resampled onto the same
# slip grid, so every belt boundary node coincides bit-for-bit with a half-mesh
# node.  No OCC cells, no arc snapping, no fragment — the whole class of
# silent-drop / seam-snap / subdivided-arc failures of route A disappears.
# SB_BELT=0 falls back to the legacy route-A OCC cells (kept for comparison).
_SB_BELT = _os_sb.environ.get("SB_BELT", "1") == "1"
# STRUCTURED gap ε retract (mm): how far the gap-facing iron is pulled off the
# transfinite cell arcs so its fuzzy polygon vertices do not subdivide them.
# None → the code default (0.01).  Diagnostic override hook (torque/ε studies).
_SG_EPS_OVERRIDE = None

# Domain ids (must match _DOMAIN_ID in the API rasterisation for consistency)
DOM_AIR     = 0
DOM_STATOR  = 1
DOM_COIL    = 2
DOM_AIRGAP  = 3
DOM_MAG_N   = 4   # N pole (generic, used for visualisation tag)
DOM_ROTOR   = 5
DOM_SHAFT   = 6
DOM_BAND    = 7   # motion / slip band inside the air gap (transient solver)
DOM_OUTER   = 8   # outer air ring (far-field boundary, beyond stator OD)
DOM_MAG_S   = 44  # S pole (generic, used for visualisation tag)

# Per-magnet domain IDs are allocated in [DOM_MAG_BASE, DOM_MAG_BASE + N_MAG).
# Each magnet gets its own tag so the FEM source term can apply that magnet's
# specific tangential M direction (M ∥ that magnet's bottom edge in the world
# frame, with polarity ±).  The high IDs stay clear of the small fixed-domain
# range above.
DOM_MAG_BASE  = 100
# Per-coil ids — each physical slot gets its own tag so the FEM assembly
# applies the correct (phase, direction) current density.  Without this
# every coil shared the same averaged J_z, which is ZERO for a balanced
# three-phase load → stator currents had no effect on the field.
DOM_COIL_BASE = 200

# Structured-gap seam density: the iron's gap-facing arcs snap to the seam grid
# (spacing = M slip nodes).  M_target=14 gave a ~3° seam step on the 150 mm
# 24s20p — coarser than the ~5° slot opening, so the snap distorted the mouths
# by up to ±1 seam DIFFERENTLY per tooth (T −23 %, forbidden h1-h3 reappeared).
# Smaller M → finer seams → smaller geometry perturbation (more, thinner cells).
_SG_M_TARGET = int(_os_sb.environ.get("SB_SG_M_TARGET", "14") or 14)
