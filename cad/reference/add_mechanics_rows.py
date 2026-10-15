"""One-off, idempotent: add the stage-3 mechanics rows to a motor profile JSON (CIANO14 40, Fusion model read 2026-10-12).

  python cad/reference/add_mechanics_rows.py cad/reference/CIANO14_40_12.json [more profiles ...]

* Fusion user parameters that no motor feature uses -> role info (they never drove anything);
* Sketch3 rows: role mechanics stays; rows the simulation or the bearing card determines become derived formulas;
* new section named_parameters_mechanics: the numbers of the Fusion mechanical features outside Sketch3 (Sketch2,
  Sketch7, Sketch10, chamfers, fillets, Prop_fix, the 618/8 import, the 91292A032 screws) and the named derived rows
  of the axial chain.  Values are the Fusion values (read-only MCP dump 2026-10-12, fusion_source/mechanics/*.json).
"""
import json
import sys

UNUSED_USER = ("motor_d", "rotor_r", "glue", "bearing_6005_d", "resolver_w", "sealing_ring_d", "housing_h", "dov_d",
               "bolt_h", "AWG", "offset_bearing", "offset_radiator", "offset_holder")
ROLE_NOTE_UNUSED = "Fusion user parameter that no feature of the motor uses (checked 2026-10-12)"

SKETCH3_CHANGES = {
    "ax_core_ref_inset": dict(role="info", role_note="construction line only (Sketch3 L8), drives nothing"),
    "shaft_journal_r": dict(freecad_expression="bearing_d / 2", role="derived",
                            role_note="Fusion '4 mm'; = bore of the bearing card (simulation config bearings)"),
    "bearing_od_r": dict(freecad_expression="bearing_D / 2", role="derived",
                         role_note="Fusion '16 mm / 2'; = outer diameter of the bearing card"),
    "bearing_w": dict(freecad_expression="bearing_B", role="derived", role_note="Fusion '4 mm'; = width of the bearing card"),
    "shaft_bore_r": dict(freecad_expression="shaft_seat_r - shaft_height", role="derived",
                         role_note="Fusion '3 mm'; = simulation shaft_inner_radius = rotor_inner_radius - shaft_height"),
    "back_holder_wall": dict(freecad_expression="back_holder_ref_r - bearing_od_r", role="derived",
                             role_note="driven dimension in Fusion (d11731 is not driving): back seat r = bearing_od_r, "
                                       "outer r = back_holder_ref_r + this"),
    "coil_corner_r": dict(role="derived", role_note="formula of the winding inputs (Sketch3 d11979)"),
}

MECH = [
    # name, freecad expression, unit, role, source, fusion expression, comment
    ("bearing_d", "8 mm", "mm", "input", "bearings_library.yaml 618/8-2Z d", "4 mm (r)",
     "Bearing bore d of the card in the simulation config (bearings A / B)"),
    ("bearing_D", "16 mm", "mm", "input", "bearings_library.yaml 618/8-2Z D", "16 mm / 2 (r)", "Bearing outer diameter D"),
    ("bearing_B", "4 mm", "mm", "input", "bearings_library.yaml 618/8-2Z B", "4 mm", "Bearing width B"),
    ("bearing_pitch_r", "(bearing_d + bearing_D) / 4", "mm", "derived", "618_8 import (torus major 6)", "6 mm",
     "Ball pitch radius = d_m / 2 = (d + D) / 4"),
    ("bearing_ball_d", "2 mm", "mm", "mechanics", "618_8 import Body3", "2 mm", "Ball diameter (groove radius = ball_d / 2)"),
    ("bearing_ball_count", "10", "", "mechanics", "618_8 import Body3", "10", "Number of balls"),
    ("bearing_inner_land_d", "10.5 mm", "mm", "mechanics", "618_8 import Body2", "10.5 mm", "Inner ring outer (land) diameter"),
    ("bearing_outer_land_d", "13.5 mm", "mm", "mechanics", "618_8 import Body1", "13.5 mm", "Outer ring inner (land) diameter"),
    ("bearing_edge_r", "0.2 mm", "mm", "mechanics", "618_8 import (torus minor 0.2)", "0.2 mm",
     "Edge radius of the ring corners (outer ring OD corners, inner ring bore corners)"),
    ("shaft_seat_r", "rotor_down_r - rotor_house_height", "mm", "derived", "CIANO14 40/Sketch3 L13", "rotor bore",
     "Shaft radius under the rotor = rotor bore = simulation rotor_inner_radius"),
    ("shaft_chamfer", "0.2 mm", "mm", "mechanics", "Shaft/Chamfer7", "0.2 mm",
     "Shaft chamfers: back end of the journal, the 10 -> 8 step, front end of the journal (one size, Equal in the sketch)"),
    ("shaft_flat_w", "5.9 mm", "mm", "mechanics", "Shaft/Sketch10 d11899", "5.9 mm",
     "Width across the two drive flats on the front journal"),
    ("shaft_flat_len", "3 mm", "mm", "mechanics", "Shaft/Extrude15", "-3.00 mm", "Length of the drive flats from the journal end"),
    ("holder_face_z", "motor_length / 2 + ring_h + ring_holder_gap", "mm", "derived", "CIANO14 40/Sketch3 L35/L39", "11.9 mm",
     "Axial chain: inner face of both holder flanges = bearing inner face = shaft shoulder (z = +-this)"),
    ("holder_flange_z", "holder_face_z + holder_flange_t", "mm", "derived", "CIANO14 40/Sketch3 L34", "13.1 mm",
     "Axial chain: outer face of the holder flanges (screw head seat front, bosses start back)"),
    ("bearing_out_z", "holder_face_z + bearing_w", "mm", "derived", "CIANO14 40/Sketch3 L30/L37", "15.9 mm",
     "Axial chain: outer face of the bearings (front lip / back shaft end)"),
    ("front_holder_end_z", "bearing_out_z + holder_lip_t", "mm", "derived", "CIANO14 40/Sketch3 L32", "16.7 mm",
     "Axial chain: front end of the front holder"),
    ("shaft_journal_end_z", "front_holder_end_z + shaft_front_overhang", "mm", "derived", "CIANO14 40/Sketch3 L17", "20.7 mm",
     "Axial chain: end of the front journal = Prop_fix seat plane"),
    ("shaft_thread_end_z", "shaft_journal_end_z + shaft_thread_len", "mm", "derived", "CIANO14 40/Sketch3 L36", "28.7 mm",
     "Axial chain: end of the thread section"),
    ("shaft_front_end_z", "shaft_thread_end_z + shaft_tip_len", "mm", "derived", "CIANO14 40/Sketch3 L19", "36.7 mm",
     "Axial chain: front end of the shaft"),
    ("back_holder_end_z", "holder_flange_z + back_holder_len", "mm", "derived", "CIANO14 40/Sketch3 L41", "20.1 mm",
     "Axial chain: back end of the back holder = hub-boss plane A_H at z = -this"),
    ("holder_boss_len", "holder_flange_z - motor_length / 2", "mm", "derived", "Stator_holder/Extrude47", "to stator face",
     "Bosses run from the flange outer face to the stator end face"),
    ("holder_inner_r", "coil_corner_r + coil_holder_clearance", "mm", "derived", "CIANO14 40/Sketch3 L44", "13.633 mm",
     "Holder flange outer radius = coil corner + clearance"),
    ("holder_hub_r", "bearing_od_r + holder_hub_wall", "mm", "derived", "CIANO14 40/Sketch3 L33", "8.8 mm",
     "Holder hub outer radius (front hub, spoke roots)"),
    ("back_holder_outer_r", "back_holder_ref_r + back_holder_wall", "mm", "derived", "CIANO14 40/Sketch3 L40", "12 mm",
     "Back holder outer radius"),
    ("core_leg_outer_x", "tooth_width / 2 + coil_w + tooth2_width", "mm", "derived", "Stator_holder/Sketch2 L0/L1 (projected core)",
     "6.09 mm", "Outer side of the core half leg (x from the sector axis) = side of the boss seat"),
    ("holder_bolt_count", "core_sectors", "", "derived", "C-Pattern28/17/18/30/33", "6",
     "Holder spokes, bosses, screws and back M3 holes: one per sector boundary"),
    ("holder_bolt_r", "14.85 mm", "mm", "mechanics", "Stator_holder/Sketch2 C0/C1 centre", "not dimensioned",
     "Bolt circle radius (screws, bosses).  NOT dimensioned in Fusion (Sketch2 is not fully constrained): 14.843191 read from the model, rounded by the owner 2026-10-13 (Fusion not dimensioned)"),
    ("holder_window_r", "1.46 mm", "mm", "mechanics", "Stator_holder/Sketch2 A4/A5", "not dimensioned (1.460021)",
     "Window fillet radius at the spoke roots.  NOT dimensioned in Fusion: 1.460021 read from the model, rounded by the owner 2026-10-13 (Fusion not dimensioned)"),
    ("holder_seat_depth", "1 mm", "mm", "mechanics", "Stator_holder/Extrude45, Stator_holder_back/Extrude47", "1 mm",
     "Boss seat depth into the stator pocket between the core legs"),
    ("holder_seat_z", "motor_length / 2 - holder_seat_depth", "mm", "derived", "Stator_holder/Extrude45", "9 mm",
     "z of the seat bottom faces (front +, back -); the seat chamfers sit on this plane"),
    ("seat_chamfer_tool_len", "2 * (holder_bolt_r + bolt_head_d)", "mm", "derived", "Chamfer23 (modelled as a pocket)",
     "-", "Length of the straight chamfer tools (symmetric about y = 0): long enough to cross every seat edge"),
    ("holder_chamfer", "0.2 mm", "mm", "mechanics", "Chamfer23 (both holders)", "0.2 mm", "Chamfer of the seat bottom edges along the core legs"),
    ("holder_tap_d", "1.623 mm", "mm", "mechanics", "Stator_holder_back/Thread14 M2x0.4 6H", "bolt_d (2 mm) + thread",
     "Back boss screw hole = M2 minor diameter (Fusion's thread feature sets the hole to it)"),
    ("back_holder_fillet_r", "1.8 mm", "mm", "mechanics", "Stator_holder_back/Fillet27", "1.8 mm",
     "Fillet of the spoke ends at the back holder outer radius"),
    ("back_screw_r", "10 mm", "mm", "mechanics", "Stator_holder_back/Sketch7 d11733", "10 mm", "Radius of the six M3 holes in the A_H face"),
    ("back_screw_angle", "angle_wire", "deg", "derived", "Stator_holder_back/Sketch7 d11763", "30 deg",
     "Angle of the M3 holes from the sector axis (on the spokes); Fusion literal 30 deg = angle_wire"),
    ("back_screw_tap_d", "2.529 mm", "mm", "mechanics", "Stator_holder_back/Sketch7 d11734 + Thread12 M3x0.5 6H", "3 mm",
     "M3 hole diameter as modelled = M3 minor diameter (sketch circle 3 mm, thread feature sets the minor diameter)"),
    ("prop_fix_d", "15 mm", "mm", "mechanics", "Prop_fix/Sketch2 d11951", "15 mm", "Prop_fix outer diameter"),
    ("prop_fix_relief_r", "1 mm", "mm", "mechanics", "Prop_fix/Sketch2 d11952", "1 mm", "Corner relief radius of the double-D hole"),
    ("prop_fix_hub_t", "1 mm", "mm", "mechanics", "Prop_fix/Extrude1", "-1.00 mm", "Hub on the flats (z below the journal end)"),
    ("prop_fix_plate_t", "1 mm", "mm", "mechanics", "Prop_fix/Extrude2", "1.00 mm", "Plate beyond the journal end"),
    ("prop_fix_clearance", "0.02 mm", "mm", "mechanics", "Prop_fix/OffsetFaces1", "-0.02 mm", "Clearance of all hole faces (offset faces)"),
    ("holder_screw_l", "2 * holder_face_z + holder_flange_t", "mm", "derived", "91292A032 (M2 x 25) in Stator_holder", "25 mm",
     "Screw length under the head: front flange face to the back holder face (Fusion L20 uses M2x25; pick the catalogue length)"),
    ("screw_head_d", "3.8 mm", "mm", "mechanics", "91292A032", "3.8 mm", "Socket head diameter"),
    ("screw_head_h", "2 mm", "mm", "mechanics", "91292A032", "2 mm", "Socket head height"),
    ("screw_head_fillet", "0.1 mm", "mm", "mechanics", "91292A032", "0.1 mm", "Head edge radius (top and bottom)"),
    ("screw_socket_af", "1.5 mm", "mm", "mechanics", "91292A032", "1.5 mm", "Hex socket across flats"),
    ("screw_socket_depth", "1 mm", "mm", "mechanics", "91292A032", "1 mm", "Hex socket depth (to the drill point)"),
    ("screw_tip_chamfer", "0.4 mm", "mm", "mechanics", "91292A032", "0.4 mm", "45 deg chamfer at the screw tip"),
    ("screw_socket_point", "120 deg", "deg", "mechanics", "91292A032", "120 deg", "Included angle of the drill point under the hex socket"),
    ("screw_head_top_z", "holder_flange_z + screw_head_h", "mm", "derived", "91292A032 occurrence", "15.1 mm",
     "z of the screw head top (hex socket sketch plane)"),
]


# Fusion materials of the mechanical bodies (the shaft takes the simulation's shaft card when the store has one)
MECH_MATERIALS = {"Shaft": "Steel", "Stator_holder": "Aluminum 6061", "Stator_holder_back": "Aluminum 6061",
                  "Prop_fix": "Aluminum 6061", "Bearing_outer_ring": "Steel (bearing)", "Bearing_inner_ring": "Steel (bearing)",
                  "Bearing_ball": "Steel (bearing)", "Screw": "18-8 Stainless Steel"}


def evaluate_value(expr):
    try:
        return float(expr.split()[0])
    except Exception:
        return None


def migrate(pj):
    rows = {p["name"]: p for sec in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part",
                                     "named_parameters_mechanics") for p in pj.get(sec, [])}
    for n in UNUSED_USER:
        if n in rows:
            rows[n]["role"] = "info"
            rows[n]["role_note"] = ROLE_NOTE_UNUSED
    for n in ("bolt_d", "bolt_head_d"):
        if n in rows:
            rows[n]["role"] = "mechanics"
            rows[n].pop("role_note", None)
    if "shaft_height" in rows:
        rows["shaft_height"]["role"] = "input"
        rows["shaft_height"]["role_note"] = "shaft wall under the rotor: bore radius = shaft_seat_r - shaft_height"
    for n, ch in SKETCH3_CHANGES.items():
        if n in rows:
            p = rows[n]
            for k, v in ch.items():
                p[k] = v
    for n, p in rows.items():
        if p.get("role") == "mechanics" and p.get("role_note", "").endswith("no effect yet"):
            p.pop("role_note")
    sec = pj.setdefault("named_parameters_mechanics", [])
    have = {p["name"] for p in sec}
    for name, expr, unit, role, src, fexpr, com in MECH:
        if name in have or name in rows:
            continue
        sec.append(dict(name=name, source=src, fusion_expression=fexpr, freecad_expression=expr,
                        value=evaluate_value(expr), unit=unit, comment=com, role=role))
    mats = pj.setdefault("profile", {}).setdefault("materials", {})
    for body, mat in MECH_MATERIALS.items():
        mats.setdefault(body, mat)
    pj.setdefault("roles", {})["mechanics"] = ("drives the mechanical parts (shaft, bearings, holders, Prop_fix, "
                                               "screws); CAD only, not in the simulation")
    return pj


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "..", "src"))
    from motor_ai_sim.cad.store import evaluate
    for path in sys.argv[1:]:
        pj = json.load(open(path, encoding="utf-8"))
        pj = migrate(pj)
        allp = [p for s in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part",
                            "named_parameters_mechanics") for p in pj.get(s, [])]
        vals = evaluate({p["name"]: p["freecad_expression"] for p in allp})
        for p in allp:
            p["value"] = round(vals[p["name"]], 9)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(pj, fh, indent=1, ensure_ascii=False)
        print(path, len(pj["named_parameters_mechanics"]), "mechanics rows")
