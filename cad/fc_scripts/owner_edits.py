"""freecadcmd script: owner-style Params edits on a built model (what the owner does in the FreeCAD GUI: change a Params
cell, Recompute), each followed by the health gate and the fit table.  The model file is never saved.

  environment: FC_CAD_ROOT, FC_MODEL (FCStd), FC_EDITS ("name=expr;name2=expr|name=expr|..." - one edit set per '|'),
               FC_OUT (JSON), FC_STATUS

Per edit set: accepted (gate ok) or refused (pre-check / gate errors in words), volumes of the mechanical bodies, the
fit errors, and the recompute time.  Every set starts from the saved model (reopened).
"""
import json, os, sys, time, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
import FreeCAD as App  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps, health  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402


def main():
    model, out = os.environ["FC_MODEL"], os.environ["FC_OUT"]
    rows = []
    for spec in [x for x in os.environ["FC_EDITS"].split("|") if x.strip()]:
        t0 = time.time()
        doc = App.openDocument(model)
        sh = doc.getObject("Params")
        ov = ps.parse_overrides(spec)
        for k, v in ov.items():
            ps.set_param(sh, k, v)
        doc.recompute()
        gt = health.gate(doc)
        row = dict(edit=spec, accepted=gt["ok"], errors=gt["errors"][:6], precheck=gt.get("precheck_violations", [])[:4],
                   volumes={k: round(v, 4) for k, v in gt["volumes"].items()})
        if gt["ok"] and doc.getObject("Mechanics") is not None:
            ft = health.fit(doc)
            row["fit_errors"] = ft["errors"]
            row["fit_not_ok"] = [(r["a"], r["b"], r["overlap_mm3"], r["min_distance_mm"]) for r in ft["pairs"] if not r["ok"]]
            row["accepted"] = not ft["errors"]
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        App.closeDocument(doc.Name)
        print("%-40s %s %s" % (spec, "ACCEPTED" if row["accepted"] else "REFUSED", (row["errors"] or row.get("fit_errors") or [""])[0][:150]))
    json.dump(dict(model=os.path.basename(model), edits=rows), open(out, "w", encoding="utf-8"), indent=1)
    finish(True, "%d edit sets: %d accepted, %d refused" % (len(rows), sum(r["accepted"] for r in rows),
                                                             sum(not r["accepted"] for r in rows)))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
