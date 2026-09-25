# Fusion 360 SCRIPT (runs INSIDE Fusion: Utilities -> Scripts and Add-Ins ->
# "+" -> this file).  Renames the LEGACY user parameters of the OPEN design
# to motor_ai_sim's canonical geometry names -- the owner-approved 33-row
# mapping of 2026-09-25 (docs/FUSION_PARAMETER_MAP_2026-09-25.md,
# config/fusion_param_map.yaml `legacy_fusion_names_approved_2026_09_25`).
# Run this ONCE per legacy design, before using fusion360_import_params.py /
# fusion360_export_params.py on it.
#
# ONE SHARED MODULE.  The map + conversions live in fusion_param_common.py
# (scripts/, one level up from this file) -- the SAME module the offline CLI
# converter (scripts/fusion_param_rename.py) and its unit tests
# (tests/test_fusion_param_rename.py) use, so this script cannot silently
# drift from what the owner reviewed and from what is actually tested.  It
# is stdlib-only for exactly this reason: Fusion's sandboxed interpreter
# cannot be assumed to have PyYAML.
#
# HOW A RENAME PROPAGATES.  UserParameter.name is writable, and per the
# Fusion API, renaming a parameter automatically rewrites every OTHER
# expression that referenced it (a pointer-based reference under the hood,
# not a text one) -- unlike the offline CSV converter, this script never has
# to text-rewrite a plain 1:1 rename.  Two of the 33 names are not plain
# renames, though (stator_diameter, magnet_lamination): they change BASIS,
# not just spelling.  For those:
#   1. rename first (name= only, value untouched) -- every dependent
#      expression now READS the new name but still evaluates the OLD number;
#   2. fix up every dependent expression algebraically (only possible for
#      stator_diameter's linear x2 factor: `stator_diameter` becomes
#      `(stator_diameter / 2)` wherever it is referenced, so it keeps
#      producing the old radius while the model is momentarily inconsistent);
#      magnet_lamination's conversion is NOT linear (0 exactly when the old
#      segment length equalled the motor length), so a dependent reference
#      to it is flagged for the owner to check by hand instead of rewritten;
#   3. only THEN convert the source parameter's own value.
# This ordering keeps every OTHER computed value numerically unchanged at
# every step except the very last, where the (now doubled/relabelled)
# source parameter's own value is the only thing that moves.
#
# TARGET-ALREADY-EXISTS.  If a design already has a user parameter under the
# CANONICAL name (e.g. it was partially renamed by hand before) AND still
# has the legacy one, this script does not guess which is right: it skips
# that item as a CONFLICT and reports it, leaving both parameters untouched.
#
# CREATE.  Five of the 33 have no legacy counterpart at all
# (num_slots_per_segment, shaft_height, sleeve_thickness) or only
# conditionally (stator_fillet_r1, rotor_fill_r -- created only if
# stator_r1 / rotor_r1 are not already in the design).  Their value is
# pulled from the running motor_ai_sim API (GET /api/fusion/params.json,
# same endpoint fusion360_import_params.py / the old fusion360_sync_params.py
# already use) so a CREATE always reflects the ACTIVE machine; if the API is
# unreachable, a small embedded fallback (motor_ai_sim's default geometry)
# is used instead and flagged in the log.
#
# DRY RUN FIRST.  The plan is always shown in a Yes/No dialog before
# anything is written; No aborts with nothing changed.
import json
import os
import sys
import traceback
import urllib.request

import adsk.core
import adsk.fusion

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import fusion_param_common as FPC  # noqa: E402

API = "http://localhost:8001/api/fusion/params.json"

# Fallback CREATE defaults if the local API is not running -- mirrors
# motor_ai_sim's own default geometry (config/motor_config.yaml) at the time
# this script was written.  The live API value is always preferred.
FALLBACK_DEFAULTS = {
    "num_slots_per_segment": 6.0,
    "shaft_height": 2.0,
    "sleeve_thickness": 0.0,
    "stator_fillet_r1": 0.1,
    "rotor_fill_r": 0.2,
}


def _fetch_defaults():
    try:
        with urllib.request.urlopen(API, timeout=5) as r:
            payload = json.load(r)
        by_key = {p["key"]: float(p["value"]) for p in payload.get("parameters", [])}
        out = dict(FALLBACK_DEFAULTS)
        used_live = []
        for k in out:
            if k in by_key:
                out[k] = by_key[k]
                used_live.append(k)
        return out, used_live, None
    except Exception as e:  # noqa: BLE001
        return dict(FALLBACK_DEFAULTS), [], str(e)


def _num(v):
    return "%g" % float(v)


def _read_value(um, param, unit):
    try:
        if not unit:
            return float(param.value)
        return float(um.convert(param.value, um.internalUnits, unit))
    except Exception:  # noqa: BLE001
        return None


def _plan(up, defaults):
    """Build the plan without touching anything. Returns a dict of lists."""
    plan = {"rename": [], "rename_convert": [], "create": [], "conflict": [],
            "not_found": [], "already": []}
    for e in FPC.ENTRIES:
        if e["action"] == "not_mapped":
            continue
        canonical, old = e["canonical"], e["old"]
        existing_new = up.itemByName(canonical)
        existing_old = up.itemByName(old) if old else None
        if existing_new and existing_old and existing_new != existing_old:
            plan["conflict"].append(e)
            continue
        if existing_new:
            plan["already"].append(e)
            continue
        if existing_old:
            if e.get("conversion"):
                plan["rename_convert"].append(e)
            else:
                plan["rename"].append(e)
            continue
        # neither exists
        if e["action"] in ("create", "rename_or_create"):
            plan["create"].append(e)
        else:
            plan["not_found"].append(e)
    return plan


def _plan_text(plan, defaults, defaults_note):
    def block(title, items, fmt):
        if not items:
            return ""
        return "\n\n%s (%d)\n%s" % (title, len(items),
                                     "\n".join("  " + fmt(e) for e in items))

    return (
        "motor_ai_sim: rename legacy Fusion parameters to canonical names\n"
        + block("WILL RENAME", plan["rename"], lambda e: "%s -> %s" % (e["old"], e["canonical"]))
        + block("WILL RENAME + CONVERT", plan["rename_convert"],
                lambda e: "%s -> %s (%s)" % (e["old"], e["canonical"], e["note"]))
        + block("WILL CREATE", plan["create"],
                lambda e: "%s = %s%s" % (e["canonical"], _num(defaults.get(e["canonical"], 0.0)),
                                          " (%s)" % e["note"] if e["note"] else ""))
        + block("CONFLICT -- both names exist, skipped", plan["conflict"],
                lambda e: "%s / %s -- resolve by hand" % (e["old"], e["canonical"]))
        + block("ALREADY canonical -- no action", plan["already"], lambda e: e["canonical"])
        + block("not in this design -- nothing to rename", plan["not_found"],
                lambda e: "%s -> %s" % (e["old"], e["canonical"]))
        + ("\n\nCREATE defaults: %s" % defaults_note if defaults_note else "")
    )


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

        defaults, used_live, err = _fetch_defaults()
        defaults_note = ("live values for %s from the API" % ", ".join(used_live)
                          if used_live else "")
        if err:
            defaults_note += (("; " if defaults_note else "")
                               + "API unreachable (%s) -- used built-in fallback defaults" % err)

        plan = _plan(up, defaults)
        total = len(plan["rename"]) + len(plan["rename_convert"]) + len(plan["create"])
        if not total:
            ui.messageBox("Nothing to do: no legacy names found and every canonical "
                           "parameter already exists.\n" + _plan_text(plan, defaults, defaults_note))
            return

        answer = ui.messageBox(_plan_text(plan, defaults, defaults_note) +
                                "\n\nApply these changes?", "motor_ai_sim: rename parameters",
                                adsk.core.MessageBoxButtonTypes.YesNoButtonType)
        if answer != adsk.core.DialogResults.DialogYes:
            ui.messageBox("Cancelled -- nothing changed.")
            return

        log = {"renamed": [], "created": [], "failed": [], "review": []}

        # 1) plain renames (Fusion auto-updates every dependent reference)
        for e in plan["rename"]:
            try:
                up.itemByName(e["old"]).name = e["canonical"]
                log["renamed"].append("%s -> %s" % (e["old"], e["canonical"]))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("%s: %s" % (e["old"], ex))

        # 2) convert-renames: rename first (value unchanged)...
        converted = []
        for e in plan["rename_convert"]:
            try:
                param = up.itemByName(e["old"])
                old_val = _read_value(um, param, e["unit"])
                param.name = e["canonical"]
                converted.append((e, old_val))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("%s: %s" % (e["old"], ex))

        # ...then fix up every OTHER parameter's expression (linear only)...
        subs, nonlinear = FPC.post_rename_fixups()
        touched_canonicals = {e["canonical"] for e, _ in converted}
        for i in range(up.count):
            p = up.item(i)
            if p.name in touched_canonicals:
                continue
            new_expr, touched = FPC.rewrite_expression(p.expression, subs)
            if new_expr != p.expression:
                try:
                    p.expression = new_expr
                except Exception as ex:  # noqa: BLE001
                    log["failed"].append("%s: could not rewrite expression (%s)" % (p.name, ex))
            for t in touched:
                pass
            for nl in nonlinear:
                if nl in p.expression:
                    log["review"].append("%s: references %s (non-linear conversion) -- verify by hand"
                                          % (p.name, nl))

        # ...then convert the source parameters' own values.
        motor_length_param = up.itemByName("motor_length")
        motor_length_val = (_read_value(um, motor_length_param, "mm")
                             if motor_length_param else None)
        for e, old_val in converted:
            param = up.itemByName(e["canonical"])
            try:
                if old_val is None:
                    log["failed"].append("%s: could not read its old value, left unconverted"
                                          % e["canonical"])
                    continue
                if e["conversion"][0] == "linear":
                    new_val = FPC.convert_forward(e, old_val)
                else:
                    if motor_length_val is None:
                        log["failed"].append("%s: motor_length not found/renamed yet, "
                                              "could not apply the lamination rule" % e["canonical"])
                        continue
                    new_val = FPC.convert_forward(e, old_val, motor_length_value=motor_length_val)
                param.expression = ("%s %s" % (_num(new_val), e["unit"])).strip()
                log["renamed"].append("%s -> %s: %s -> %s"
                                       % (e["old"], e["canonical"], _num(old_val), _num(new_val)))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("%s: %s" % (e["canonical"], ex))

        # 3) create the missing ones
        for e in plan["create"]:
            try:
                val = defaults.get(e["canonical"], 0.0)
                expr = ("%s %s" % (_num(val), e["unit"])).strip()
                up.add(e["canonical"], adsk.core.ValueInput.createByString(expr), e["unit"],
                       "created by fusion360_rename_params.py (owner-approved 2026-09-25 mapping)")
                log["created"].append("%s = %s" % (e["canonical"], expr))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("%s: %s" % (e["canonical"], ex))

        adsk.doEvents()
        ui.messageBox(_summary(log))
    except Exception:
        if ui:
            ui.messageBox("rename failed:\n" + traceback.format_exc())


def _block(title, lines, limit=40):
    if not lines:
        return ""
    shown = ["  " + s for s in lines[:limit]]
    if len(lines) > limit:
        shown.append("  ... and %d more" % (len(lines) - limit))
    return "\n\n%s\n%s" % (title, "\n".join(shown))


def _summary(log):
    head = ("motor_ai_sim: legacy parameters renamed\n\n"
            "renamed/converted: %d\ncreated: %d\nfailed: %d\nneeds review: %d"
            % (len(log["renamed"]), len(log["created"]), len(log["failed"]), len(log["review"])))
    body = (_block("RENAMED / CONVERTED", log["renamed"])
            + _block("CREATED", log["created"])
            + _block("FAILED", log["failed"])
            + _block("NEEDS MANUAL REVIEW", log["review"]))
    return head + body
