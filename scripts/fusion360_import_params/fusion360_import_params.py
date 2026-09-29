# Fusion 360 SCRIPT (runs INSIDE Fusion: Utilities -> Scripts and Add-Ins ->
# "+" -> this file).  Pulls the LIVE geometry of motor_ai_sim from the local
# API and writes it into this design's User Parameters, by canonical name --
# existing ones updated, missing ones created -- then lets the model rebuild.
#
# THIS IS THE RENAMED/REFACTORED scripts/fusion360_sync_params.py (2026-09-25
# packaging: three scripts under the fusion360_*_params/ naming --
# fusion360_rename_params (legacy -> canonical, once per legacy design),
# fusion360_export_params (design -> CSV, the reverse direction),
# fusion360_import_params (this file, our app -> design)).  The logic is
# UNCHANGED from fusion360_sync_params.py -- /api/fusion/params.json already
# emits canonical names (config/fusion_param_map.yaml's `map:` section is
# identity for all 33 approved geometry inputs), so nothing about how this
# script writes parameters needed to change, only where it lives.
# scripts/fusion360_sync_params/ is left in place, unchanged, for any
# existing Fusion add-in registration that already points at it -- both
# work identically; this is the one to use going forward.
#
# The same values are available as a Parameter I/O CSV from
# http://localhost:8001/api/fusion/params.csv when the script route is not
# wanted.  Nothing is sent back to motor_ai_sim from here; for that see
# fusion360_export_params.py (write a CSV from this design) or, in the app,
# the Geometry tab's "Fusion CSV" upload button / `POST /api/fusion/import`.
#
# UNITS (2026-09-14).  A Fusion user parameter is either a LENGTH parameter
# (unit "mm") or a UNITLESS one (unit "", the counts and the ratios).  The two
# are not interchangeable and the API is blunt about it:
#   * UnitsManager.convert(v, "", "") raises "3 : Bad units parameter" -- a
#     unitless quantity has no unit to convert between, so the comparison has
#     to read Parameter.value directly (it IS the number);
#   * a unitless parameter's expression must be a BARE number -- assigning
#     "0.13 mm" raises "3 : Expression is invalid";
#   * .unit is only assigned when the model's unit DIFFERS from ours, and a
#     refusal there is reported as a unit mismatch the user has to resolve in
#     Fusion, not as a raw API error.
#
# COMMENTS (2026-09-14).  The model's user parameters mostly have an empty
# Comment, so every parameter also gets OUR schema description: written on
# creation as before, and written onto an existing parameter whenever the
# model's comment is empty or says something else -- including a parameter whose
# VALUE is unchanged.  A comment-only change is counted on its own line
# ("comment set") and is never reported as a value update.
#
# Replaces fusion360_controller.py (2026-02): that one carried eleven names
# the geometry no longer has.
import csv
import io
import json
import os
import sys
import traceback
import urllib.request

import adsk.core
import adsk.fusion

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fusion_param_common as FPC  # noqa: E402
import importlib  # noqa: E402
importlib.reload(FPC)  # Fusion keeps modules in memory between runs: always load the current map

API = "http://localhost:8001/api/fusion/params.json"
TOL = 1e-6

# Names the script refuses to touch even when the payload still carries them.
# slot_hs (2026-09-14): the model keeps it UNITLESS (0.15) while our export
# called it a length (0.13 mm); the export is dropping the row, and until every
# API is updated the defensive skip keeps the model's parameter alone instead
# of arguing about its unit.
SKIP = frozenset(("slot_hs",))


def _num(v):
    """The number as Fusion writes it in an expression: no trailing zeros."""
    return "%g" % float(v)


def _expr(value, unit):
    """'7.4 mm' for a length parameter, a BARE '0.34' for a unitless one."""
    return ("%s %s" % (_num(value), unit)).strip()


def _current(um, existing, unit):
    """`existing` read back in OUR unit, or None when it cannot be read.

    Parameter.value is in DATABASE units -- cm for a length, the plain number
    for a unitless parameter.  Only the length case goes through the units
    manager; convert() with an empty unit string is the "Bad units parameter"
    error, not a no-op.
    """
    try:
        if not unit:
            return float(existing.value)
        return float(um.convert(existing.value, um.internalUnits, unit))
    except Exception:      # noqa: BLE001 - an unreadable value just means "not comparable"
        return None


def _clip(text, n=48):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "..."


def _write_value(um, existing, name, value, unit, expr):
    """Write the VALUE of an existing parameter.  ('same'|'updated'|'failed', detail).

    API AUDIT (2026-09-25, owner: "исправь все эти косяки в скриптах").
    `Parameter.unit` is READ-ONLY in the Fusion API (help.autodesk.com's own
    Parameter.unit reference page, corroborated on the Autodesk community
    forum: assigning it always raises) -- an earlier version of this
    function assigned `existing.unit = unit` and therefore ALWAYS failed on
    the "different unit" branch, even in the common, perfectly fixable case
    of the same DIMENSION (length vs length) just displayed in a different
    unit (e.g. the model has "in", we want "mm"): Fusion parses the unit
    token INSIDE the expression string regardless of the parameter's
    current display unit, so writing `existing.expression = "12 mm"`
    already handles that case with no separate unit-assignment step at all.
    Only a genuine DIMENSION mismatch (length vs unitless) cannot be
    bridged this way -- Fusion then rejects the expression
    ("Expression is invalid"), which is reported as a mismatch for the
    user to resolve by hand in Fusion (recreating the parameter under the
    right type) rather than guessed at automatically.
    """
    try:
        cur_unit = str(existing.unit or "")
    except Exception:      # noqa: BLE001
        cur_unit = unit    # unreadable -> assume ours, let the setter decide

    # Compare in the parameter's OWN current unit so an unchanged value is
    # not rewritten (keeps the timeline clean) -- only when the units
    # already match; a genuine unit difference always needs a write attempt
    # since a value that happens to be numerically equal in two different
    # units usually is not the same length.
    if cur_unit == unit:
        old = _current(um, existing, unit)
        if old is not None and abs(old - value) <= TOL:
            return "same", name
    else:
        old = _current(um, existing, cur_unit)

    try:
        existing.expression = expr
    except Exception as ex:      # noqa: BLE001
        mismatch = ("%s: unit mismatch: model %r vs ours %r -- change the parameter's "
                    "type in Fusion by hand (%s)" % (name, cur_unit, unit, ex))
        return "failed", mismatch
    return "updated", ("%s: %s -> %s"
                       % (name, ("%s %s" % (_num(old), cur_unit)).strip()
                          if old is not None else "?", expr))


def _load_from_csv_dialog(ui):
    """Fallback (2026-09-25, owner audit) for when the local API is not
    running: let the user pick a Parameter I/O-shaped CSV file instead --
    either our own export's six columns or a plain export from the add-in
    itself, tolerant of both (FPC.parse_param_io_csv_rows matches columns
    by header NAME). Returns (params, source_text) with `params` shaped
    exactly like the API's own `parameters` list, or (None, None) if the
    user cancelled."""
    dlg = ui.createFileDialog()
    dlg.isMultiSelectEnabled = False
    dlg.title = "Import Parameter I/O CSV (our own export, or the add-in's own)"
    dlg.filter = "CSV files (*.csv)"
    if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
        return None, None
    path = dlg.filename
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig", errors="replace")
    rdr = csv.DictReader(io.StringIO(text))
    rows = list(rdr)
    values, refused = FPC.parse_param_io_csv_rows(rows, rdr.fieldnames)
    params = [{"name": n, "unit": u, "value": v, "comment": c}
              for n, (v, u, c) in values.items()]
    source = "CSV %s" % path
    if refused:
        source += " (%d row(s) not a plain number, skipped: %s)" % (
            len(refused), ", ".join(sorted(refused)))
    return params, source


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Open a Fusion design first.")
            return

        source = "the running motor_ai_sim API"
        try:
            with urllib.request.urlopen(API, timeout=10) as r:
                payload = json.load(r)
            params = payload.get("parameters") or []
        except Exception as api_err:  # noqa: BLE001
            answer = ui.messageBox(
                "motor_ai_sim API unreachable at %s\n(%s)\n\n"
                "Import from a Parameter I/O CSV file instead?" % (API, api_err),
                "motor_ai_sim: import parameters",
                adsk.core.MessageBoxButtonTypes.YesNoButtonType)
            if answer != adsk.core.DialogResults.DialogYes:
                ui.messageBox("Cancelled -- nothing changed.")
                return
            try:
                params, source = _load_from_csv_dialog(ui)
            except ValueError as ve:
                ui.messageBox("Could not read this CSV: %s" % ve)
                return
            if params is None:
                return  # file dialog cancelled
            payload = {"machine": source}

        up = design.userParameters
        um = design.unitsManager
        created, updated, same, failed, noted = [], [], [], [], []
        for p in params:
            name = str(p["name"]); unit = str(p.get("unit") or "")
            value = float(p["value"]); comment = str(p.get("comment") or "")
            expr = _expr(value, unit)
            if name in SKIP:
                continue
            try:
                existing = up.itemByName(name)
                if existing is None:
                    up.add(name, adsk.core.ValueInput.createByString(expr), unit, comment)
                    created.append("%s = %s" % (name, expr))
                    continue

                verdict, detail = _write_value(um, existing, name, value, unit, expr)
                if verdict == "updated":
                    updated.append(detail)
                elif verdict == "failed":
                    failed.append(detail)

                # The comment is written INDEPENDENTLY of the value: the
                # model's user parameters mostly carry no Comment at all and
                # the schema description is what makes the list readable, so an
                # otherwise unchanged parameter still gets its text (user
                # 2026-09-14 -- dirtying the timeline for that is accepted).  A
                # comment-only change is counted on its own and is NEVER
                # reported as a value update.
                wrote_comment = False
                if comment:
                    try:
                        if str(existing.comment or "") != comment:
                            existing.comment = comment
                            wrote_comment = True
                    except Exception as e:      # noqa: BLE001
                        failed.append("%s: comment not set (%s)" % (name, e))
                if wrote_comment:
                    noted.append("%s: %s" % (name, _clip(comment)))
                elif verdict == "same":
                    same.append(name)
            except Exception as e:      # noqa: BLE001 - one bad row must not stop the rest
                failed.append("%s: %s" % (name, e))
        adsk.doEvents()
        ui.messageBox(_summary(payload, updated, created, same, failed, noted))
    except Exception:
        if ui:
            ui.messageBox("sync failed:\n" + traceback.format_exc())


def _block(title, lines, limit=40):
    if not lines:
        return ""
    shown = ["  " + s for s in lines[:limit]]
    if len(lines) > limit:
        shown.append("  ... and %d more" % (len(lines) - limit))
    return "\n\n%s\n%s" % (title, "\n".join(shown))


def _summary(payload, updated, created, same, failed, noted):
    head = ("motor_ai_sim -> Fusion parameters (%s)\n\n"
            "updated: %d\ncreated: %d\ncomment set: %d\nunchanged: %d\nfailed: %d"
            % (payload.get("machine", "?"), len(updated), len(created),
               len(noted), len(same), len(failed)))
    body = (_block("UPDATED (old -> new)", updated)
            + _block("CREATED", created)
            + _block("COMMENT SET", noted)
            + _block("FAILED", failed))
    if same:
        body += "\n\nUNCHANGED\n  " + ", ".join(same)
    return head + body
