"""Params <-> profile JSON synchronisation and the source-conflict rule (M01).

Rule: the profile JSON is what agents build from.  If the owner edited the Params sheet of the FCStd:
  * FCStd newer than the JSON and the sheet differs  -> the FCStd wins: build_model refuses to overwrite it until
    tools/sync_params.py has written the sheet back into the JSON (or FROM_JSON=1 is given to discard the edits;
    the old FCStd is then kept in models/backup/).
  * JSON newer than the FCStd and the sheet differs   -> the JSON wins (agent edit): build_model rebuilds from JSON
    (old FCStd backed up); sync_params refuses unless FORCE=1.
"""
import os
import FreeCAD as App
from . import params_sheet as ps


def read_fcstd_sheet(fcstd_path, sheet_name="Params"):
    doc = App.openDocument(fcstd_path)
    try:
        sh = doc.getObject(sheet_name)
        if sh is None:
            raise RuntimeError("%s has no %s sheet" % (fcstd_path, sheet_name))
        return ps.read_sheet(sh)
    finally:
        App.closeDocument(doc.Name)


def canonical_expressions(pj):
    """JSON expressions as FreeCAD normalises them (temporary document with the Params sheet only)."""
    doc = App.newDocument("canon_tmp")
    try:
        ps.make_params_sheet(doc, pj)
        doc.recompute()
        return {k: v["expression"] for k, v in ps.read_sheet(doc.getObject("Params")).items()}
    finally:
        App.closeDocument(doc.Name)


def conflict_state(fcstd_path, json_path, pj=None):
    pj = pj or ps.load_params_json(json_path)
    if not os.path.exists(fcstd_path):
        return dict(exists=False, differs=False)
    sheet = read_fcstd_sheet(fcstd_path)
    canon = canonical_expressions(pj)
    diff, only_js, only_sh = ps.sheet_differs(pj, sheet, canon)
    fc_m, js_m = os.path.getmtime(fcstd_path), os.path.getmtime(json_path)
    return dict(exists=True, differs=bool(diff or only_sh), changed=diff, only_in_json=only_js, only_in_fcstd=only_sh,
                fcstd_newer=fc_m > js_m, sheet=sheet, canon=canon)


def sync_fcstd_to_json(fcstd_path, json_path, force=False, backup=True):
    """Write the owner's Params (expressions + values, by alias) from the FCStd into the profile JSON.
    Refuses (returns ok=False) when the JSON is newer and differs (agent edit would be lost) unless force, and when
    the FCStd does not pass the health gate (an invalid edit must not become the profile) unless force."""
    import datetime
    import shutil
    from . import health
    pj = ps.load_params_json(json_path)
    cs = conflict_state(fcstd_path, json_path, pj)
    if not cs.get("exists"):
        return dict(ok=False, message="%s does not exist" % fcstd_path)
    if not cs["differs"]:
        return dict(ok=True, message="already in sync - nothing to write", changes=[])
    if not cs["fcstd_newer"] and not force:
        return dict(ok=False, message="JSON is newer than the FCStd and differs (%s): the JSON wins - rebuild with "
                                      "tools/build_model.py, or FORCE=1 to overwrite the JSON from the FCStd"
                                      % ", ".join(cs["changed"] + cs["only_in_fcstd"]))
    doc = App.openDocument(fcstd_path)
    try:
        for o in doc.Objects:
            o.touch()
        doc.recompute()
        gt = health.gate(doc)
    finally:
        App.closeDocument(doc.Name)
    if not gt["ok"] and not force:
        return dict(ok=False, message="the edited FCStd fails the health gate - not synced: " + " | ".join(gt["errors"][:6]),
                    gate=gt["errors"])
    new, changes = ps.apply_sheet_to_profile(pj, cs["sheet"], note="sync from %s" % os.path.basename(fcstd_path),
                                             canon=cs["canon"])
    if backup:
        bdir = os.path.join(os.path.dirname(json_path), "backup")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(json_path, os.path.join(bdir, "%s.%s.json" % (os.path.splitext(os.path.basename(json_path))[0],
                                                                    datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))))
    ps.save_params_json(new, json_path)
    return dict(ok=True, message="synced %d change(s) into %s" % (len(changes), os.path.basename(json_path)), changes=changes)
