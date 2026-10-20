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
A.7.4 (2026-10-16): the GFRP Ring (body Ring, link Ring_back) is back in the FULL model of every profile that has ring rows (CIANO14 40,
CIANO28 85; it fixes the coils) and is omitted only from the SIMPLIFIED model; CIANO28 150_35 has no ring. Holders / bearings / shaft keep
their axial positions (same ring_h + ring_holder_gap chain).
A.7.5 (2026-10-19): CIANO14 50 (a reference profile with no Fusion model: cad/reference/CIANO14_50_edited.json): the phase board STEP is named by
profile.phase_board (step, pins, copper_volume; a board STEP without pins), the Tip of the array bases Screw / Bearing_ball is visible so the
link arrays Screws / Balls show. Owner 2026-10-19: holder_screw_l = holder_screw_req rounded UP to an even mm (CIANO14 40 L20: M2x26, was 25;
L12 18, 85 L13 20 unchanged).
A.7.6 (2026-10-19): CIANO28 150_35: the four aluminium parts (Shaft, Shaft_rear, Stator_holder, Stator_holder_back) are the REAL Fusion bodies
(profile.fusion_parts: read-only Fusion STEP of each part, imported once as the BaseFeature of the same body) instead of the volume-equivalent
rings, whose partial rings were sector sets with gaps (windows, hairline slits, notched rims) that the Fusion design does not have (owner
2026-10-19: no gaps anywhere, Fusion is the truth). Profiles without fusion_parts, and every CIANO14 40 / 50 and CIANO28 85 build, are unchanged.
A.7.7 (2026-10-19): CIANO14 40: Shaft, Prop_fix, Stator_holder and Stator_holder_back of the die "CIANO14 40 new" are the REAL Fusion v19 bodies
(profile.fusion_parts of cad/reference/CIANO14_40_12.json, STEP files in the private data repository; owner: "everything in production comes ONLY from
Fusion"). The same mechanism as A.7.6 now serves the standard mechanics (fusion_parts.py): rigid parts shift with motor_length, the Shaft is stretched by
a cut-and-shift in its constant dia 9.9 rotor-seat section (L20 = L12 + 8 mm). The template shaft differed from Fusion (bore bottom, flange dia 15.9 instead
of 13.9, thread section dia 4.95 instead of 4.901) and so did the Prop_fix hole (4.99 vs 4.901). Other dies and the other mechanics are unchanged.
A.7.8 (2026-10-20): CIANO28 150_35: EVERY part of the full model is the Fusion body (owner 2026-10-20: the 3-D model of the 150_35 comes from
Fusion; Fusion sync decision Q5 of 2026-10-19: core, rotor, magnets and coils too, the EM solver keeps its own 2-D geometry). Core, RotorCore,
Magnet (one slice), Coil (the 29 conductor bodies fused), the bearing rings and shield and the three screw families are the Fusion solids at the
Fusion occurrence the template body stands for (profile.fusion_parts select / placement); the template arrays and links place the copies and the
build proves that each one lands on a Fusion occurrence (fusion_parts.instance_check, occurrences). The phase board is the Fusion board
(electronics.build_board_fusion; the SIMPLIFIED model keeps the template plate), the fan plates and the alumina insulation pieces are Fusion-only
parts (fusion_extras.py). A Fusion body that disagrees with a Params row is WARNED (build_report fusion_warnings), not refused. Profiles without
these keys build exactly as A.7.7.
A.7.9 (2026-10-20): G2 = die CILN28 (config G2-L40, the generator CILN28 200, Fusion document "CILN28 200"). The profile has NO template mechanics and no template phase
board: EVERY part is a Fusion body. The four active parts (Core, RotorCore, Magnet, Coil) are the Fusion solids exactly as in A.7.8 (the template's own parametric
geometry is the check: Core, rotor and magnets equal the Fusion bodies to 1e-9, the coil is 0.175 % bigger than the Fusion body sum); every other part is an
`extras` item of profile.fusion_parts (fusion_extras.py: holders, rotor holders, housing, fittings, fan, bearings, four screw families, the 72 alumina pieces, the
seven copper layers and the eight GFRP plates of the phase board). New in this version: `extras[].select` (the part's own solid of a component STEP that also carries
its child screws), `density_kg_m3` / `density_src` / `simplified` / `count_solids` per item, `fusion_parts.bom_mode = "fusion_assembly"` (bill of materials of
src/motor_ai_sim/cad/bom_fusion.py), `fusion_parts.fit_design` and the interference table of fusion_extras.fit(), the SIMPLIFIED model keeps the extras that are
marked keep / ring / board, rotor tip / key fillet rows (rotor_tip_fill_r, rotor_key_fill_r: the template's own G2 rotor, used for the sync check), a full 360 degree
cone face is tessellated through its NURBS form (the viewer mesh lost 3.4 % of a Fusion holder). Profiles without these keys build exactly as A.7.8.
"""
TEMPLATE_NAME = "segmented_flatwire"
TEMPLATE_VERSION = "A.7.9"
