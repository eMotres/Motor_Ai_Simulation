"""FreeCAD GUI script (headless): verify that a saved model opens ready to look at.

  environment: FC_CAD_ROOT, FC_MODEL, FC_REPORT (JSON), FC_STATUS

Checks (all must hold): GuiDocument.xml with a camera inside the FCStd; every object has a view provider; the visibility
rules of motor_templates/segmented_flatwire/view.py hold for the document (Document.xml) AND the view providers (GUI);
the body / array colours are the template colours.
"""
import json, os, sys, traceback, zipfile

sys.path.insert(0, os.environ["FC_CAD_ROOT"])
out = dict(ok=False, message="not finished")
try:
    import FreeCAD as App
    import FreeCADGui as Gui
    from motor_templates.segmented_flatwire import view
    model = os.environ["FC_MODEL"]
    with zipfile.ZipFile(model) as z:
        gx = z.read("GuiDocument.xml").decode("utf-8") if "GuiDocument.xml" in z.namelist() else ""
    out["guidocument_xml"] = bool(gx)
    out["camera_in_guidocument"] = "<Camera " in gx and "OrthographicCamera" in gx
    doc = App.openDocument(model)
    gdoc = Gui.getDocument(doc.Name)
    bad = view.check_visibility(doc)
    out["missing_view_providers"] = [o.Name for o in doc.Objects if gdoc.getObject(o.Name) is None]
    gui_vis = {o.Name: bool(gdoc.getObject(o.Name).Visibility) for o in doc.Objects
               if gdoc.getObject(o.Name) is not None and hasattr(gdoc.getObject(o.Name), "Visibility")}
    for o in doc.Objects:
        if o.Name in gui_vis and hasattr(o, "Visibility") and gui_vis[o.Name] != bool(o.Visibility):
            bad.append("%s: document Visibility %s but view provider %s" % (o.Name, o.Visibility, gui_vis[o.Name]))
    cols = {}
    for n in view.BODIES:
        vo = gdoc.getObject(n)
        c = tuple(round(x, 2) for x in vo.ShapeColor[:3])
        cols[n] = c
        if c != tuple(round(x, 2) for x in view.COLORS[n]):
            bad.append("%s colour %s != %s" % (n, c, view.COLORS[n]))
    out["body_colours"] = cols
    out["visible"] = sorted(n for n, v in gui_vis.items() if v)
    out["hidden_count"] = sum(1 for v in gui_vis.values() if not v)
    out["violations"] = bad
    ok = out["guidocument_xml"] and out["camera_in_guidocument"] and not out["missing_view_providers"] and not bad
    out.update(ok=ok, message="opens ready to look at" if ok else "NOT ready: %s" % (
        bad[:4] or "GuiDocument.xml/camera missing or view providers missing"))
except Exception:
    traceback.print_exc()
    out.update(ok=False, message="exception: " + traceback.format_exc().splitlines()[-1])
for p in (os.environ.get("FC_STATUS"), os.environ.get("FC_REPORT")):
    if p:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
print(("OK: " if out["ok"] else "REFUSED: ") + out["message"])
sys.stdout.flush()
os._exit(0)
