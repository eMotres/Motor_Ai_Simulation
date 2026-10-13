"""One-off, idempotent: add the phase (coil-connection) board rows to a motor profile JSON (2026-10-13).

  python cad/reference/add_phase_board_rows.py cad/reference/CIANO14_40_12.json [more profiles ...]

The board is an imported, non-parametric reference body (cad/reference/phase_board/*.step, rev C of the PCB_CIANO14_40
project, a coloured KiCad export).  Its placement is parametric: the App::Part `Electronics` takes its X / Y / Z from the
rows below, Z from the axial chain (coil lead end datum + the N-side offset), so the board follows `motor_length`.
"""
import json
import os
import sys

ROWS = [
    # name, freecad expression, unit, role, source, comment
    ("phase_board_n_offset", "2.525 mm", "mm", "mechanics", "PCB_CIANO14_40 rev C",
     "Phase board: N-side copper surface sits this far ABOVE the coil lead end datum (coil_lead_end_z = -(coil_leg_length / 2 + "
     "coil_lead_total)), toward the motor, so the lead ends stand out of the board by 2.525 mm.  The board thickness (1.6 +-10 %) "
     "grows toward the motor: the N side stays fixed"),
    ("phase_board_n_z0", "-0.21 mm", "mm", "mechanics", "PCB_CIANO14_40 rev C STEP",
     "Phase board STEP frame (KiCad absolute, +Z = layer A facing the motor): Z of the N-side copper surface.  A property of the "
     "STEP file, change it only with a new board export"),
    ("phase_board_x", "-100 mm", "mm", "mechanics", "PCB_CIANO14_40 rev C STEP",
     "Phase board STEP frame offset X: the KiCad board centre is at X 100, so the translation to the motor axis is -100"),
    ("phase_board_y", "100 mm", "mm", "mechanics", "PCB_CIANO14_40 rev C STEP",
     "Phase board STEP frame offset Y: the KiCad board centre is at Y -100 (Y flipped), so the translation to the motor axis is +100"),
    ("phase_board_z", "-(coil_leg_length / 2 + coil_lead_total) + phase_board_n_offset - phase_board_n_z0", "mm", "derived",
     "axial chain",
     "Phase board placement Z (translation, no rotation): N copper at coil lead end + phase_board_n_offset.  L12 -12.615 "
     "(N copper -12.825), L20 -16.615 (N copper -16.825)"),
]


def migrate(pj):
    sec = pj.setdefault("named_parameters_mechanics", [])
    have = {p["name"] for p in sec}
    for name, expr, unit, role, src, com in ROWS:
        if name not in have:
            sec.append(dict(name=name, source=src, fusion_expression="-", freecad_expression=expr, value=0.0, unit=unit,
                            comment=com, role=role))
    return pj


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from motor_ai_sim.cad.store import evaluate
    for path in sys.argv[1:]:
        pj = migrate(json.load(open(path, encoding="utf-8")))
        allp = [p for s in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part",
                            "named_parameters_mechanics") for p in pj.get(s, [])]
        vals = evaluate({p["name"]: p["freecad_expression"] for p in allp})
        for p in allp:
            p["value"] = round(vals[p["name"]], 9)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(pj, fh, indent=1, ensure_ascii=False)
        print(path, len(pj["named_parameters_mechanics"]), "mechanics rows")
