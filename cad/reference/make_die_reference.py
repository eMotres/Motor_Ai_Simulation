"""Make the Fusion-free reference profile of a die from the CIANO14 40 reference + a design file (idempotent).

  python cad/reference/make_die_reference.py designs/CIANO14_50_edited_design.json CIANO14_50_edited.json

`cad/reference/<die slug>.json` is what `store.reference_for(die)` loads as the base profile of a die (the simulation values are laid on
top of it by `store.make_profile`).  A motor with no Fusion model of its own - CIANO14 50 (owner 2026-10-18/19) - takes the CIANO14 40
reference and the rows of its design file, the rows the simulation does not carry (bearing, shaft tip, holders, coil lead extension).

Design file keys (the format of the owner repo's params/CIANO14_50_design.json):
  set        {section: {row name: {expr, source[, role, comment, fusion_expression]}}}  rows must exist in the base profile
  remove     [row names]
  add        {section: [full rows]}
  deviations {row name: {expr, why}}  simulation values the template cannot build: kept in the profile (top-level "deviations") and
             applied by make_profile AFTER the simulation values, with the reason in the row's source
  profile    {key: value}  merged into the profile block (e.g. "phase_board": {step, pins, copper_volume})
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SECTIONS = ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part", "named_parameters_mechanics")


def apply_design(pj, design):
    for sec, rows in (design.get("set") or {}).items():
        by = {p["name"]: p for p in pj[sec]}
        for n, d in rows.items():
            if n not in by:
                raise KeyError("design row %s not in %s" % (n, sec))
            by[n]["freecad_expression"] = d["expr"]
            if "fusion_expression" in d:
                by[n]["fusion_expression"] = d["fusion_expression"]
            by[n]["source"] = d["source"]
            for k in ("role", "comment"):
                if k in d:
                    by[n][k] = d[k]
    gone = set(design.get("remove") or [])
    for sec in SECTIONS:
        pj[sec] = [p for p in pj[sec] if p["name"] not in gone]
    for sec, rows in (design.get("add") or {}).items():
        pj[sec].extend(rows)
    if design.get("deviations"):
        pj["deviations"] = copy.deepcopy(design["deviations"])
    pj["profile"].update(copy.deepcopy(design.get("profile") or {}))
    return pj


def main(argv):
    sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
    from motor_ai_sim.cad.store import evaluate
    design_path, out = argv[0], argv[1]
    design = json.load(open(os.path.join(HERE, design_path), encoding="utf-8"))
    pj = apply_design(json.load(open(os.path.join(HERE, "CIANO14_40_12.json"), encoding="utf-8")), design)
    allp = [p for s in SECTIONS for p in pj.get(s, [])]
    vals = evaluate({p["name"]: p["freecad_expression"] for p in allp})
    for p in allp:
        p["value"] = round(vals[p["name"]], 9)
    pj["profile"]["note"] = "reference of a die with no Fusion model of its own: the CIANO14 40 reference + %s" % design_path
    with open(os.path.join(HERE, out), "w", encoding="utf-8") as fh:
        json.dump(pj, fh, indent=1, ensure_ascii=False)
    print(out, "written;", len(allp), "rows")


if __name__ == "__main__":
    main(sys.argv[1:])
