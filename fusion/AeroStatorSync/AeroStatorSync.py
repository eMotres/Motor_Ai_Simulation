"""AeroStator Sync - Fusion add-in: Export to AeroStator / Import from AeroStator / Settings (docs/FUSION_SYNC.md).

Install: Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins > "+" > this folder; tick "Run on Startup".
The buttons sit in the panel "AeroStator" of the Solid tab.  Fusion is the only place geometry is edited: Export sends
the saved version to AeroStator, Import writes AeroStator's EM parameters into the user parameters after a diff and
never saves the document.
"""
import json
import os
import sys
import tempfile
import traceback

import adsk.core
import adsk.fusion

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from aerostator_sync import client as C          # noqa: E402
from aerostator_sync import fusion_collect as FC  # noqa: E402
from aerostator_sync import keystore as K         # noqa: E402
from aerostator_sync import params as PR          # noqa: E402

PANEL_ID = "AeroStatorSyncPanel"
CMDS = {
    "export": ("AeroStatorSyncExport", "Export to AeroStator", "Send this saved Fusion version (STEP per component, parameters, sketches) to AeroStator"),
    "import": ("AeroStatorSyncImport", "Import from AeroStator", "Show AeroStator's EM parameters of the linked die/config as a diff and write the ticked ones"),
    "settings": ("AeroStatorSyncSettings", "AeroStator settings", "Server, sync key and the optional mirror folder"),
}
_handlers = []
_state = {}


def _ui():
    return adsk.core.Application.get().userInterface


def _msg(text, title="AeroStator Sync"):
    _ui().messageBox(text, title)


def _client():
    s = K.load_settings()
    key = K.load_key()
    if not key:
        raise C.SyncError(0, "no sync key yet: open 'AeroStator settings' and paste the key (Access for agents, scope fusion:sync)")
    return C.Client(s["server"], key), s


def _active_doc():
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if doc is None or adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType")) is None:
        raise FC.ExportRefused("open a Fusion design first")
    return doc


def _link_for(cl, doc):
    """The die/config this document is linked to; the first time, a picker of the allow-listed targets stores the link."""
    if doc.dataFile is None:
        raise FC.ExportRefused("the document was never saved to the Fusion hub")
    link = cl.get_link(doc.dataFile.id)
    if link:
        return link
    t = cl.targets()
    choices = ["%s / %s" % (x["die"], c) for x in t.get("targets", []) for c in x.get("configs", [])]
    if not choices:
        raise C.SyncError(0, "the server lists no die/config that may be synced (allow-list empty)")
    text = "This document is not linked to an AeroStator die yet.\nType the number of the target:\n\n" + \
           "\n".join("%d  %s" % (i + 1, c) for i, c in enumerate(choices))
    val, cancelled = _ui().inputBox(text, "Link to AeroStator", "1")
    if cancelled:
        raise FC.ExportRefused("not linked - nothing sent")
    i = int(val) - 1
    die, _, config = choices[i].partition(" / ")
    return cl.put_link(doc.dataFile.id, doc.name, die, config)


# ── Export ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def do_export():
    doc = _active_doc()
    cl, settings = _client()
    link = _link_for(cl, doc)
    prog = _ui().createProgressDialog()
    prog.show("AeroStator export", "%v", 0, 1)
    try:
        z = FC.export_zip(doc, sketches=link.get("sketches") or [], link={k: link.get(k) for k in ("die", "config")},
                          progress=lambda m: setattr(prog, "message", m))
        if settings.get("mirror_dir"):
            import shutil
            dst = os.path.join(settings["mirror_dir"], FC.B.safe_name(link["die"]))
            os.makedirs(dst, exist_ok=True)
            shutil.copy2(z, dst)
        prog.message = "uploading"
        r = cl.upload(z)
    finally:
        prog.hide()
    lines = ["%s version %d: %s." % (doc.name, r.get("version", 0), "already on the server" if r.get("already_stored") else "stored"),
             "Linked to %s / %s." % (link["die"], link["config"])]
    diff = r.get("param_diff") or []
    if diff:
        lines.append("\nFusion and AeroStator differ on %d parameter(s):" % len(diff))
        lines += ["  %s: Fusion %g, AeroStator %g %s" % (d["name"], d["fusion"], d["aerostator"], d.get("unit", "")) for d in diff[:15]]
    if r.get("rebuild"):
        lines.append("\nCAD rebuild: %s" % r["rebuild"].get("state", "queued"))
    _msg("\n".join(lines))


# ── Import ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _fusion_params(des):
    return {p["name"]: p for p in FC.user_parameters(des)}


def prepare_import():
    doc = _active_doc()
    des = FC.design_of(doc)
    cl, _s = _client()
    link = _link_for(cl, doc)
    srv = cl.params(link["die"], link["config"])
    fus = _fusion_params(des)
    legacy = PR.legacy_names_present(fus)
    if legacy:
        raise FC.ExportRefused("this document still uses the legacy parameter names (%s ...). Run the approved rename "
                               "(scripts/fusion360_rename_params) first; the import never writes a legacy name."
                               % ", ".join(sorted(legacy)[:4]))
    rows = PR.import_diff(fus, srv.get("parameters", []))
    _state["import"] = dict(doc=doc, rows=rows, link=link, server=srv)
    return rows


class _ImportCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            rows = prepare_import()
            cmd = args.command
            inputs = cmd.commandInputs
            changed = [r for r in rows if r["action"] != "unchanged"]
            link = _state["import"]["link"]
            inputs.addTextBoxCommandInput("hdr", "", "<b>%s / %s</b>: %d change(s), %d unchanged.  The document is not saved; "
                                          "review and save a new version yourself." % (link["die"], link["config"], len(changed),
                                                                                      len(rows) - len(changed)), 2, True)
            tbl = inputs.addTableCommandInput("tbl", "Parameters", 4, "1:3:2:2")
            tbl.maximumVisibleRows = 20
            for i, r in enumerate(changed):
                cb = inputs.addBoolValueInput("tick_%d" % i, "", True, "", bool(r["tick"]))
                nm = inputs.addTextBoxCommandInput("n_%d" % i, "", r["name"] + (" (formula)" if r["formula"] else "")
                                                   + (" NEW" if r["action"] == "create" else ""), 1, True)
                old = inputs.addTextBoxCommandInput("o_%d" % i, "", r["fusion_expression"] or "-", 1, True)
                new = inputs.addTextBoxCommandInput("v_%d" % i, "", r["new_expression"], 1, True)
                for col, ci in enumerate((cb, nm, old, new)):
                    tbl.addCommandInput(ci, i, col)
            _state["import"]["changed"] = changed
            h = _ImportExecute()
            cmd.execute.add(h)
            _handlers.append(h)
        except Exception as e:      # noqa: BLE001 - every failure ends in a message, never a silent no-op
            args.command.isAutoExecute = True
            _msg(str(e))


class _ImportExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            st = _state["import"]
            inputs = args.command.commandInputs
            for i, r in enumerate(st["changed"]):
                r["tick"] = adsk.core.BoolValueCommandInput.cast(inputs.itemById("tick_%d" % i)).value
            des = FC.design_of(st["doc"])
            res = FC.apply_parameters(des, st["changed"])
            bad = FC.timeline_problems(des)
            text = "Updated %d, created %d parameter(s)." % (len(res["updated"]), len(res["created"]))
            if res["failed"]:
                text += "\nFailed: " + "; ".join("%s: %s" % kv for kv in res["failed"].items())
            if bad:
                text += "\n\nTimeline problems after the update (Undo reverts the whole import):\n" + \
                        "\n".join("  #%d %s: %s" % b for b in bad[:12])
            text += "\n\nNothing was saved. Review the model and save a new version, then Export to AeroStator."
            _msg(text)
        except Exception:      # noqa: BLE001
            _msg("Import failed:\n" + traceback.format_exc()[-1500:])


# ── Settings ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def do_settings():
    s = K.load_settings()
    srv, c1 = _ui().inputBox("AeroStator server URL", "AeroStator settings", s.get("server") or C.DEFAULT_SERVER)
    if c1:
        return
    key, c2 = _ui().inputBox("Sync key (emk_..., scope fusion:sync). Leave empty to keep the stored key.", "AeroStator settings", "")
    if c2:
        return
    mirror, c3 = _ui().inputBox("Optional mirror folder for every exported bundle (e.g. the private repo checkout). Empty = none.",
                                "AeroStator settings", s.get("mirror_dir") or "")
    if c3:
        return
    K.save_settings(dict(server=srv.strip(), mirror_dir=mirror.strip()))
    if key.strip():
        K.save_key(key.strip())
    _msg("Saved. Key stored: %s" % ("yes" if K.load_key() else "no"))


class _Simple(adsk.core.CommandCreatedEventHandler):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def notify(self, args):
        h = _SimpleExecute(self.fn)
        args.command.execute.add(h)
        _handlers.append(h)
        args.command.isAutoExecute = True


class _SimpleExecute(adsk.core.CommandEventHandler):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def notify(self, args):
        try:
            self.fn()
        except (C.SyncError, FC.ExportRefused) as e:
            _msg(str(e))
        except Exception:      # noqa: BLE001
            _msg("Failed:\n" + traceback.format_exc()[-1500:])


def run(context):
    try:
        ui = _ui()
        ws = ui.workspaces.itemById("FusionSolidEnvironment")
        tab = ws.toolbarTabs.itemById("SolidTab")
        panel = tab.toolbarPanels.itemById(PANEL_ID) or tab.toolbarPanels.add(PANEL_ID, "AeroStator")
        for key, (cid, name, tip) in CMDS.items():
            cd = ui.commandDefinitions.itemById(cid) or ui.commandDefinitions.addButtonDefinition(cid, name, tip, "")
            h = _ImportCreated() if key == "import" else _Simple(do_export if key == "export" else do_settings)
            cd.commandCreated.add(h)
            _handlers.append(h)
            if panel.controls.itemById(cid) is None:
                panel.controls.addCommand(cd)
    except Exception:      # noqa: BLE001
        _ui().messageBox("AeroStator Sync failed to start:\n" + traceback.format_exc()[-1500:])


def stop(context):
    ui = _ui()
    ws = ui.workspaces.itemById("FusionSolidEnvironment")
    panel = ws.toolbarTabs.itemById("SolidTab").toolbarPanels.itemById(PANEL_ID) if ws else None
    for cid, _n, _t in CMDS.values():
        if panel and panel.controls.itemById(cid):
            panel.controls.itemById(cid).deleteMe()
        cd = ui.commandDefinitions.itemById(cid)
        if cd:
            cd.deleteMe()
    if panel:
        panel.deleteMe()
    _handlers.clear()
