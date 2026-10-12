"""Sync-back of the owner's Params edits to the simulation side (review files + optional proposal), used by
macros/SyncBackToSim.FCMacro (GUI) and tools/sim_sync.py back (command line, reads the saved FCStd).

Nothing is ever written to the simulation server: the files land in <repo>/sync/ and the orchestrator applies a
proposal after the owner confirms it.
"""
import os
import sys


def repo_of(doc):
    """The repository that holds this module (tools/sim_sync.py next to motor_templates/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def variant_of(doc):
    mid = (doc.Meta or {}).get("motor_motor_id", "") or os.path.splitext(os.path.basename(doc.FileName))[0]
    for v in ("L12", "L20"):
        if mid.endswith("_" + v):
            return v
    raise ValueError("cannot tell the variant (L12 / L20) of %s (motor id %r) - the sync-back works on "
                     "models/CIANO14_40_L12.FCStd and models/CIANO14_40_L20.FCStd" % (doc.FileName, mid))


def live_cells(doc, sheet="Params"):
    sh = doc.getObject(sheet)
    out = {}
    for cell in sh.getUsedCells():
        a = sh.getAlias(cell)
        if a:
            out[a] = sh.getContents(cell).lstrip("=")
    return out


def run(doc, propose=False):
    """Diff of the open document's Params against the simulation snapshot; returns (result dict, gate dict)."""
    repo = repo_of(doc)
    if os.path.join(repo, "tools") not in sys.path:
        sys.path.insert(0, os.path.join(repo, "tools"))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import importlib
    import sim_sync
    importlib.reload(sim_sync)
    from motor_templates.segmented_flatwire import health
    doc.recompute()
    gt = health.gate(doc)
    res = sim_sync.back(variant_of(doc), live_cells(doc), propose=propose and gt["ok"],
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
    L.append("Files (in the repo):")
    L += ["  " + f for f in res.get("files", [])]
    L.append("Nothing was sent to the simulation server.")
    return "\n".join(L)
