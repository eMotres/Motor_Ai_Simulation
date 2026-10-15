"""Template: segmented stator with flat (rectangular) wire coils, rotor with magnet pockets (PartDesign, parametric).

Version-safe for FreeCAD 1.0.2 and 1.1 (symmetric pads: SideType on 1.1, Midplane on 1.0.x; commit 8ef2e35 of the
CIANO14_40_motor_model repo).  Bump TEMPLATE_VERSION whenever the geometry or the parameter set changes: it goes into
every build manifest.

A.6.0 (2026-10-15): `cad_variant` = full | frameless (frameless.py): the frameless kit omits the housing and the rotating mechanics.
The full variant builds exactly as A.5.0.
"""
TEMPLATE_NAME = "segmented_flatwire"
TEMPLATE_VERSION = "A.6.0"
