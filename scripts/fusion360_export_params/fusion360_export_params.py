# Fusion 360 SCRIPT (runs INSIDE Fusion: Utilities -> Scripts and Add-Ins ->
# "+" -> this file).  The REVERSE of fusion360_import_params.py: reads the
# OPEN design's 33 canonical geometry-input user parameters and writes them
# to a Parameter I/O-shaped CSV file (Name, Unit, Expression, Value, Comment,
# Favorite) -- exactly the format motor_ai_sim's own web Geometry tab already
# accepts ("Fusion CSV" upload button -> POST /api/fusion/import) and that
# Fusion's own Parameter I/O add-in produces.  No new backend endpoint or web
# button was needed for this: the import side already existed and already
# speaks this format.
#
# LEGACY-TOLERANT.  If this design has not been through
# fusion360_rename_params.py yet, a canonical name may not exist -- this
# script then falls back to the matching LEGACY name (same map, imported
# from fusion_param_common.py, one level up) and applies the same forward
# conversion the rename script uses (stator_diameter = 2 * stator_up_r;
# magnet_lamination from mag_step's segment-length convention), so the
# CSV this produces is always in canonical terms regardless of what the
# open design currently calls its parameters.
#
# wire_split is exported only if the design actually has a parameter by
# that exact name (it is never invented -- same rule as the CSV converter
# and the rename script: ours defaults to 1, nothing is created for it).
import csv
import io
import os
import sys
import traceback

import adsk.core
import adsk.fusion

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fusion_param_common as FPC  # noqa: E402

HEADER = ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"]


def _read_value(um, param, unit):
    try:
        if not unit:
            return float(param.value)
        return float(um.convert(param.value, um.internalUnits, unit))
    except Exception:  # noqa: BLE001
        return None


def _collect(up, um):
    """canonical -> (value, source) for every one of the 33 that can be
    found, either under its own name or (legacy-tolerant) the old one."""
    found = {}
    legacy_used = []
    missing = []

    # motor_length is needed up front for the magnet_lamination fallback
    # conversion (mag_step vs. the motor length).
    def _get_canonical_or_legacy(entry):
        canonical, old, unit = entry["canonical"], entry["old"], entry["unit"]
        p = up.itemByName(canonical)
        if p is not None:
            v = _read_value(um, p, unit)
            if v is not None:
                return v, "canonical"
        if old:
            p = up.itemByName(old)
            if p is not None:
                v = _read_value(um, p, unit)
                if v is not None:
                    return v, "legacy:%s" % old
        return None, None

    motor_length_entry = FPC.BY_CANONICAL["motor_length"]
    motor_length_val, ml_src = _get_canonical_or_legacy(motor_length_entry)

    for e in FPC.ENTRIES:
        canonical = e["canonical"]
        if e["action"] == "not_mapped":
            p = up.itemByName(canonical)
            if p is not None:
                v = _read_value(um, p, e["unit"])
                if v is not None:
                    found[canonical] = (v, "canonical")
            continue
        if canonical == "motor_length":
            if motor_length_val is not None:
                found[canonical] = (motor_length_val, ml_src)
            else:
                missing.append(canonical)
            continue

        raw, src = _get_canonical_or_legacy(e)
        if raw is None:
            missing.append(canonical)
            continue
        if src == "canonical" or e.get("conversion") is None:
            found[canonical] = (raw, src)
            continue
        # legacy value found under the old name -- apply the forward conversion.
        conv = e["conversion"]
        if conv[0] == "linear":
            val = FPC.convert_forward(e, raw)
        else:  # lamination
            if motor_length_val is None:
                missing.append(canonical)
                continue
            val = FPC.convert_forward(e, raw, motor_length_value=motor_length_val)
        found[canonical] = (val, src)
        legacy_used.append("%s (from %s)" % (canonical, src))

    return found, legacy_used, missing


def _num(v):
    return "%g" % float(v)


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Open a Fusion design first.")
            return
        up = design.userParameters
        um = design.unitsManager

        found, legacy_used, missing = _collect(up, um)
        if not found:
            ui.messageBox("No canonical or legacy geometry parameters found in this "
                           "design -- nothing to export.")
            return

        dlg = ui.createFileDialog()
        dlg.isMultiSelectEnabled = False
        dlg.title = "Export motor_ai_sim geometry parameters"
        dlg.filter = "CSV files (*.csv)"
        default_name = (design.rootComponent.name or "motor").replace(" ", "_") + "_canonical_params.csv"
        dlg.initialFilename = default_name
        if dlg.showSave() != adsk.core.DialogResults.DialogOK:
            return
        out_path = dlg.filename

        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        w.writerow(HEADER)
        for canonical in sorted(found):
            value, src = found[canonical]
            unit = FPC.BY_CANONICAL[canonical]["unit"]
            comment = "" if src == "canonical" else ("exported via legacy fallback: " + src)
            w.writerow([canonical, unit, _num(value), _num(value), comment, "False"])
        with open(out_path, "wb") as f:
            f.write(buf.getvalue().encode("utf-8-sig"))

        msg = ("motor_ai_sim: exported %d parameter(s) to\n%s\n\n"
               "Upload it with the Geometry tab's \"Fusion CSV\" (up-arrow) "
               "button, or POST it to /api/fusion/import." % (len(found), out_path))
        if legacy_used:
            msg += "\n\nfrom LEGACY names (conversion applied):\n  " + "\n  ".join(legacy_used)
        if missing:
            msg += "\n\nNOT FOUND in this design (%d):\n  " % len(missing) + ", ".join(sorted(missing))
        ui.messageBox(msg)
    except Exception:
        if ui:
            ui.messageBox("export failed:\n" + traceback.format_exc())
