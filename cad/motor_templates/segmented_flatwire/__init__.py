"""Template: segmented stator with flat (rectangular) wire coils, rotor with magnet pockets (PartDesign, parametric).

Version-safe for FreeCAD 1.0.2 and 1.1 (symmetric pads: SideType on 1.1, Midplane on 1.0.x; commit 8ef2e35 of the
CIANO14_40_motor_model repo).  Bump TEMPLATE_VERSION whenever the geometry or the parameter set changes: it goes into
every build manifest.

A.6.0 (2026-10-15): `cad_variant` = full | frameless (frameless.py): the frameless kit omits the housing and the rotating mechanics.
The full variant builds exactly as A.5.0.
A.6.1 (2026-10-15): the holder screw length follows the stack (longest stock length that keeps the tip inside the back flange;
rows screw_tip_margin, holder_screw_max); L12 / L20 / CIANO28 85 L13 keep their lengths.
A.7.0 (2026-10-15): the SIMPLIFIED envelope-only model (simplified.py, fc_scripts/export_simplified.py) is written for every config next to the real one;
the geometry of the real model is unchanged.
A.7.1 (2026-10-15): the GFRP Ring (body Ring, link Ring_back) is removed from every construction; the holders keep their position (ring_h + ring_holder_gap stay in the axial chain).
A.7.2 (2026-10-15): the simplified coil block is the envelope of ALL inner turns (the rear end turn is closed like the real one) and the export gate tests it.
A.7.3 (2026-10-16): CIANO28 150_35 port. Axial magnet slices (rows mag_step, magnet_slices: MagnetStack inside MagnetArray), the bridge-aware
coil transition (-z end turn with a real centre line, used when coil_bridge_length > 1), the occupancy mechanics variant (row occupancy_variant:
mechanics_occupancy.py; aluminium holders as volume-equivalent rings, three screw families, plate phase board) with its gate / fit / STEP /
GLB / BOM branches. Existing profiles (CIANO14 40, CIANO28 85) build the same geometry as A.7.2.
"""
TEMPLATE_NAME = "segmented_flatwire"
TEMPLATE_VERSION = "A.7.3"
