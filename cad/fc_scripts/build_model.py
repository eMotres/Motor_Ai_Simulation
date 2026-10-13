"""freecadcmd script (FreeCAD 1.0.2 / 1.1): build <FC_OUT> from a motor profile JSON, with the pre-check of the validity
domain and the mandatory health gate.  Started by motor_ai_sim.cad.build (never by hand, but it can be):

  freecadcmd cad/fc_scripts/build_model.py   with the environment
     FC_CAD_ROOT   the cad/ directory (holds motor_templates/)
     FC_PROFILE    profile JSON (written by motor_ai_sim.cad.store)
     FC_OUT        FCStd to write
     FC_REPORT     JSON report of the build (volumes, gate, sketches)       [optional]
     FC_STATUS     JSON status file (motor_templates/segmented_flatwire/runstatus.py: 0 ok / 3 refused / 4 crash)
     FC_SET="name=expr;name2=expr"  what-if overrides (tests)              [optional]

1. Params sheet + pre-check of the validity domain -> violations in words, nothing built;
2. build + health gate (states, sketches, counts, stale geometry, parameter roles) -> on failure nothing is saved.
The server build always starts from the store, so there is no owner-edit conflict rule here (that rule belongs to the
local GUI flow, motor_templates/segmented_flatwire/sync.py).
"""
import json, os, sys, time, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
import FreeCAD as App  # noqa: E402,F401
from motor_templates.segmented_flatwire import params_sheet as ps, health, view  # noqa: E402
from motor_templates.segmented_flatwire.build import prepare_document, build_bodies  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402


def main():
    t0 = time.time()
    pj = ps.load_params_json(os.environ["FC_PROFILE"])
    out = os.environ["FC_OUT"]
    overrides = ps.parse_overrides(os.environ.get("FC_SET", ""))
    if overrides:
        pj = ps.apply_overrides(pj, overrides)
    doc, sh = prepare_document(pj)
    viol = health.precheck(lambda n: ps.pval(sh, n))
    if viol and os.environ.get("FC_SKIP_PRECHECK") != "1":
        return finish(False, "pre-check failed, nothing built: " + " | ".join(viol), precheck=viol, overrides=overrides)

    report = build_bodies(doc, sh, pj)
    gt = health.gate(doc)
    if gt["ok"] and doc.getObject("Mechanics") is not None:
        ft = health.fit(doc)                       # stage 3: interference / contact table of the assembled motor
        report["fit"] = ft["pairs"]
        report["screw_check"] = ft.get("screw")
        if ft["errors"]:
            gt["errors"] += ft["errors"]
            gt["ok"] = False
    if gt["ok"] and doc.getObject("Electronics") is not None:
        from motor_templates.segmented_flatwire import electronics
        pb = electronics.fit(doc, electronics.motor_groups(doc))       # clearances + lead ends vs the board (reported, not refused)
        report["phase_board"] = dict(pb, import_=report.get("phase_board_import"))
    report["gate"] = {k: v for k, v in gt.items() if k != "volumes"}
    report["volumes"] = {k: round(v, 4) for k, v in gt["volumes"].items()}
    direct, live = ps.live_parameters(doc)
    roles = {p["name"]: p.get("role") for _, p in ps.all_params(pj)}
    declared = {n for n, r in roles.items() if r in ps.LIVE_ROLES}
    report["roles_check"] = dict(live_in_model=len(live), declared_live=len(declared),
                                 live_but_not_declared=sorted(live - declared), declared_but_dead=sorted(declared - live))
    if report["roles_check"]["live_but_not_declared"] or report["roles_check"]["declared_but_dead"]:
        gt["errors"].append("parameter roles out of date: %s" % report["roles_check"])
        gt["ok"] = False
    if not gt["ok"]:
        for e in gt["errors"]:
            print("  GATE:", e)
        return finish(False, "health gate failed, FCStd NOT saved: " + " | ".join(gt["errors"][:8]),
                      gate=gt["errors"], overrides=overrides)

    view.set_visibility(doc)                       # ready-to-look-at: bodies + arrays visible, the rest hidden
    bad = view.check_visibility(doc)
    if bad:
        return finish(False, "visibility rules violated: " + " | ".join(bad[:6]))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    doc.saveAs(out)
    report["seconds"] = round(time.time() - t0, 1)
    report["sketches_fully_constrained"] = sum(1 for v in report["sketches"].values() if v["fully_constrained"] and v["solve"] == 0)
    report["sketches_total"] = len(report["sketches"])
    report["expressions"] = {o.Name: len(o.ExpressionEngine) for o in doc.Objects if getattr(o, "ExpressionEngine", None)}
    report["freecad_version"] = ".".join(str(x) for x in App.Version()[:3])
    report["profile"] = pj.get("profile", {})
    if os.environ.get("FC_REPORT"):
        with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=str)
    print(json.dumps({k: report[k] for k in ("param_mismatches", "sketches_fully_constrained", "sketches_total", "volumes",
                                             "roles_check", "seconds")}, indent=1))
    return finish(True, "saved %s (gate ok, %d expressions)" % (os.path.basename(out), sum(report["expressions"].values())))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
