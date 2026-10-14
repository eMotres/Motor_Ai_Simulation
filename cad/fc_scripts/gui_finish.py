"""FreeCAD GUI script (FreeCAD.exe / `freecad`, run headless: QT_QPA_PLATFORM=offscreen or xvfb-run): make a built model
"ready to look at" - visibility, colours, view providers for the Draft arrays, an isometric camera fitted to the model -
and save it with GUI data (GuiDocument.xml).  Started by motor_ai_sim.cad.build after build_model.py.

  environment: FC_CAD_ROOT, FC_MODEL (FCStd, saved in place), FC_STATUS, FC_REPORT (JSON, optional)

The geometry is not changed: the model is opened, recomputed, must pass the health gate, then styled and saved.
The isometric camera is written into GuiDocument.xml directly (offscreen has no OpenGL to fit a live view).
"""
import json, os, sys, traceback, zipfile, re

sys.path.insert(0, os.environ["FC_CAD_ROOT"])
out = dict(ok=False, message="not finished")


def write_status():
    p = os.environ.get("FC_STATUS")
    if p:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=str)
    if os.environ.get("FC_REPORT"):
        with open(os.environ["FC_REPORT"], "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=str)


def inject_camera(path, cam):
    """Put <Camera settings=.../> into GuiDocument.xml of the saved FCStd (replace the existing one)."""
    tmp = path + ".tmp"
    esc = cam.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "&#10;")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "GuiDocument.xml":
                t = data.decode("utf-8")
                tag = '<Camera settings="%s"/>' % esc
                if "<Camera " in t:
                    t = re.sub(r"<Camera [^>]*/>", lambda m: tag, t, count=1)
                else:
                    t = t.replace("</Document>", "    " + tag + "\n</Document>")
                data = t.encode("utf-8")
            zout.writestr(item, data)
    os.replace(tmp, path)


try:
    import FreeCAD as App
    import FreeCADGui as Gui
    from motor_templates.segmented_flatwire import health, view
    model = os.environ["FC_MODEL"]
    doc = App.openDocument(model)
    gdoc = Gui.getDocument(doc.Name)
    out["freecad_version"] = ".".join(str(x) for x in App.Version()[:3])
    out["missing_view_providers_before"] = [o.Name for o in doc.Objects if gdoc.getObject(o.Name) is None]
    for o in doc.Objects:
        o.touch()
    doc.recompute()
    gt = health.gate(doc)
    if not gt["ok"]:
        out.update(ok=False, message="health gate failed, GUI data NOT written: " + " | ".join(gt["errors"][:6]))
    else:
        out["styling"] = view.style_gui(doc, gdoc)
        opaque_bad = view.check_opaque(doc, gdoc)             # 2026-10-14: DiffuseColor alpha 0.0 = fully transparent in FreeCAD 1.1
        if opaque_bad:
            raise RuntimeError("transparent visible objects, model NOT saved: " + " | ".join(opaque_bad[:6]))
        bbox = view.model_bbox(doc)
        out["bbox"] = bbox
        doc.save()
        App.closeDocument(doc.Name)
        # Without OpenGL (offscreen / xvfb) a live view cannot fit anything (its camera is a degenerate default), so the
        # isometric camera fitted to the bounding box is always written into GuiDocument.xml directly.
        inject_camera(model, view.camera_string(bbox))
        out["camera"] = "isometric, fitted to the bounding box"
        out.update(ok=True, message="GUI data written (GuiDocument.xml, camera: %s)" % out["camera"])
except Exception:
    traceback.print_exc()
    out.update(ok=False, message="exception: " + traceback.format_exc().splitlines()[-1])
write_status()
print(("OK: " if out["ok"] else "REFUSED: ") + out["message"])
sys.stdout.flush()
os._exit(0)
