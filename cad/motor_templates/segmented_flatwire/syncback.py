"""Sync-back of the owner's Params edits to the simulation side (review files + optional proposal), used by
macros/SyncBackToSim.FCMacro (GUI) and `python -m motor_ai_sim.cad.build back` (command line, reads the saved FCStd).

Nothing is ever written to the simulation store: the files land in <model folder>/sync/ and the orchestrator applies a
proposal after the owner confirms it.
"""
import os
import sys


def repo_of(doc=None):
    """Root of the motor_ai_sim checkout / image that holds this module (cad/motor_templates/... is two levels below)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def die_and_config(doc):
    """(die, config) of a model built by motor_ai_sim.cad: the profile variant text is
    '<config> (simulation die <die>, config <config>.yaml)'."""
    import re
    meta = (doc.Meta or {}).get("motor_variant", "")
    m = re.search(r"simulation die (.+?), config (\w+)\.yaml", meta)
    if not m:
        raise ValueError("cannot tell the die / config of %s (variant %r): the sync-back works on models built by "
                         "motor_ai_sim.cad.build" % (doc.FileName, meta))
    return m.group(1), m.group(2)


def live_cells(doc, sheet="Params"):
    sh = doc.getObject(sheet)
    out = {}
    for cell in sh.getUsedCells():
        a = sh.getAlias(cell)
        if a:
            out[a] = sh.getContents(cell).lstrip("=")
    return out


def run(doc, propose=False):
    """Diff of the open document's Params against the simulation die/config; returns (result dict, gate dict).
    The store (read-only) is $MOTRES_DIES_DIR or <folder of the model>/sim_snapshot (a copy of the server's dies/ folder);
    the review files go to <folder of the model>/sync."""
    import sys
    import types
    repo = repo_of(doc)
    if "motor_ai_sim" not in sys.modules:                 # the light CAD modules only: do not import the FEM stack
        pkg = types.ModuleType("motor_ai_sim")
        pkg.__path__ = [os.path.join(repo, "src", "motor_ai_sim")]
        sys.modules["motor_ai_sim"] = pkg
    if os.path.join(repo, "cad") not in sys.path:
        sys.path.insert(0, os.path.join(repo, "cad"))
    from motor_ai_sim.cad import store
    from motor_templates.segmented_flatwire import health
    doc.recompute()
    gt = health.gate(doc)
    die, config = die_and_config(doc)
    here = os.path.dirname(os.path.abspath(doc.FileName))
    dies = os.environ.get("MOTRES_DIES_DIR") or os.path.join(here, "sim_snapshot")
    eff = store.load_effective(dies, die, config, os.environ.get("MOTRES_MATERIALS_LIBRARY") or
                               os.path.join(here, "sim_snapshot", "materials_library.yaml"))
    res = store.back(eff, store.load_mapping(repo), live_cells(doc), os.path.join(here, "sync"), propose=propose and gt["ok"],
                     origin="%s (open document%s)" % (os.path.basename(doc.FileName), "" if gt["ok"] else ", FAILS the health gate"))
    return res, gt


def summary(res, gt):
    L = ["Variant %s: %d simulation parameters compared, %d equal, %d different."
         % (res["variant"], res["compared"], res["equal"], len(res["differences"]))]
    for d in res["differences"]:
        L.append("  %s: simulation %g -> FreeCAD %g  (%s)" % (d["name"], d["sim"], d["freecad"], d["target"]))
    if not gt["ok"]:
        L.append("The model FAILS the health gate - no proposal written:")
        L += ["  " + e for e in gt["errors"][:6]]
    L.append("Files:")
    L += ["  " + f for f in res.get("files", [])]
    L.append("Nothing was sent to the simulation server.")
    return "\n".join(L)
