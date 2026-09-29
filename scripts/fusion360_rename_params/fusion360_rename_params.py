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
# NOTHING IS EVER RENAMED AWAY AND NOTHING IS EVER DELETED (owner, 2026-09-25:
# "чтобы никаких переменных не уничтожалось, только переименования" --
# revised again the same day: "формулы не меняй, только одну:
# stator_up_r = stator_diameter/2"). Three kinds of change only:
#   1. PLAIN RENAME (27 of the 33): UserParameter.name = <canonical>. Per the
#      Fusion API this automatically rewrites every OTHER expression that
#      referenced it -- nothing here has to text-rewrite a plain rename.
#   2. CREATE (up.add(...)): a brand-new parameter for the 6 that have no
#      legacy counterpart, or only conditionally
#      (num_slots_per_segment, shaft_height, sleeve_thickness always;
#      stator_fillet_r1 <- stator_r1, rotor_fill_r <- rotor_r1 only if the
#      legacy name is absent; stator_diameter and magnet_lamination always,
#      see #3 below), holding OUR value with an explicit unit ("12 mm",
#      never a bare number for a length).
#   3. THE ONE EXPRESSION EDIT: `stator_up_r` is NEVER renamed. Instead
#      `stator_diameter` is CREATED (value = stator_up_r's current value x2
#      -- it was a RADIUS, canonical is the DIAMETER), and stator_up_r's own
#      Expression is set to `"stator_diameter / 2"` -- the ONLY existing
#      formula this script ever touches. Every other parameter that already
#      referenced stator_up_r needs nothing done to it: the name persists,
#      so those references keep working unmodified.
# `magnet_lamination` is CREATED from `mag_step`'s current value the same
# way, but `mag_step` itself is left COMPLETELY untouched -- no rename, no
# expression edit -- because there is no clean, always-valid inverse formula
# for it (see fusion_param_common.lamination_backward's docstring, and
# docs/FUSION_SCRIPTS_HOWTO.md).
#
# GUARD.  Before anything is written, every planned operation is tagged
# "rename" / "create" / "derive_stator_up_r" and the set of tags is checked
# against exactly that whitelist -- if this script's logic ever changed to
# plan something else (an edit to an unrelated expression, anything
# resembling a delete), it refuses to run at all rather than risk it.
#
# TARGET-ALREADY-EXISTS.  If a design already has a user parameter under the
# CANONICAL name (e.g. it was partially renamed by hand before) AND still
# has the legacy one, this script does not guess which is right: it skips
# that item as a CONFLICT and reports it, leaving both parameters untouched.
#
# CREATE VALUES.  Pulled from the running motor_ai_sim API
# (GET /api/fusion/params.json, same endpoint fusion360_import_params.py /
# the old fusion360_sync_params.py already use) so a CREATE always reflects
# the ACTIVE machine; if the API is unreachable, a small embedded fallback
# (motor_ai_sim's default geometry) is used instead and flagged in the log.
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
import importlib  # noqa: E402
importlib.reload(FPC)  # Fusion keeps modules in memory between runs: always load the current map

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
    "stator_diameter": 50.0,
    "magnet_lamination": 0.0,
}

# The ONLY operation kinds this script is allowed to plan. Checked before
# anything is written -- see the module docstring's GUARD paragraph.
# "repair_rename" (2026-09-25) is the ONE addition: undoing the FIRST
# version of this script (commit e65e16e), which plainly renamed
# stator_up_r -> stator_diameter (keeping the RADIUS value) and
# mag_step -> magnet_lamination (keeping the motor-length value) instead of
# doing the CREATE + one-expression-edit / CREATE-from-legacy this version
# does. A repair_rename is still just a plain Name change (Fusion rewrites
# dependents automatically) -- never a delete, never a value edit -- so it
# fits the same "nothing is ever deleted" guarantee as an ordinary rename.
ALLOWED_OP_KINDS = frozenset(("rename", "create", "derive_stator_up_r", "repair_rename"))


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


def _expr(value, unit):
    return ("%s %s" % (_num(value), unit)).strip()


def _read_value(um, param, unit):
    try:
        if not unit:
            return float(param.value)
        return float(um.convert(param.value, um.internalUnits, unit))
    except Exception:  # noqa: BLE001
        return None


class _PostRepairView:
    """A view of `up.itemByName` as the design will look AFTER the planned
    REPAIR renames (undo of the v1 script bug) are applied -- used only to
    build the dry-run PLAN/text before anything is actually written. The
    APPLY phase always re-reads the real `up` fresh, after the repair
    renames have genuinely happened, so a mistake in this view could at
    worst mislead the preview text -- it can never cause a wrong write.
    """
    def __init__(self, up, hide, alias_to_real):
        self._up = up
        self._hide = set(hide)          # names to report as absent
        self._alias = dict(alias_to_real)  # alias name -> the real param to return for it

    def itemByName(self, name):
        if name in self._alias:
            return self._up.itemByName(self._alias[name])
        if name in self._hide:
            return None
        return self._up.itemByName(name)


def _enumerate_names_and_expressions(up):
    """Every user-parameter Name in the design, and a {name: expression}
    map -- used to look for a formula that references `stator_diameter`
    without dividing it by 2 (the v1-broken signature; see
    fusion_param_common.stator_diameter_used_as_radius)."""
    names = []
    exprs = {}
    try:
        count = up.count
    except Exception:  # noqa: BLE001
        count = 0
    for i in range(count):
        try:
            p = up.item(i)
            names.append(p.name)
            exprs[p.name] = str(p.expression)
        except Exception:  # noqa: BLE001
            continue
    return names, exprs


def _plan_repair(up, um):
    """Detect whether this design is in the v1-broken state (see
    fusion_param_common.plan_v1_repair's docstring) and, if so, what to
    repair before running the normal v2 plan."""
    names, exprs = _enumerate_names_and_expressions(up)
    present = set(names)

    stator_diameter_val = None
    sd_param = up.itemByName("stator_diameter")
    if sd_param is not None:
        stator_diameter_val = _read_value(um, sd_param, "mm")

    stack = {}
    for canonical, key in (("slot_height", "slot_height_mm"),
                           ("core_thickness", "core_thickness_mm"),
                           ("air_gap", "air_gap_mm"),
                           ("magnet_height", "magnet_height_mm"),
                           ("rotor_house_height", "rotor_house_height_mm"),
                           ("shaft_height", "shaft_height_mm")):
        p = up.itemByName(canonical)
        stack[key] = _read_value(um, p, "mm") if p is not None else 0.0

    dependent_exprs = [expr for pname, expr in exprs.items() if pname != "stator_diameter"]

    motor_length_val = None
    mlp = up.itemByName("motor_length") or up.itemByName("stator_w")
    if mlp is not None:
        motor_length_val = _read_value(um, mlp, "mm")

    magnet_lamination_val = None
    mlam = up.itemByName("magnet_lamination")
    if mlam is not None:
        magnet_lamination_val = _read_value(um, mlam, "mm")

    return FPC.plan_v1_repair(
        present, dependent_expressions=dependent_exprs,
        stator_diameter_value=stator_diameter_val, stack_components=stack,
        motor_length_value=motor_length_val, magnet_lamination_value=magnet_lamination_val)


def _plan(up, defaults):
    """Build the plan without touching anything.

    Returns (plan, ops) where `plan` groups entries for the dialog text and
    `ops` is the flat, TAGGED list of actual operations the GUARD checks.
    """
    plan = {"rename": [], "create": [], "conflict": [], "not_found": [], "already": []}
    ops = []  # list of {"kind": ..., ...}

    for e in FPC.ENTRIES:
        if e["action"] not in ("rename", "rename_or_create"):
            continue  # the two conversion entries and "create"/"not_mapped" handled separately
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
            plan["rename"].append(e)
            ops.append({"kind": "rename", "old": old, "canonical": canonical})
            continue
        if e["action"] == "rename_or_create":
            plan["create"].append(e)
            ops.append({"kind": "create", "canonical": canonical, "unit": e["unit"]})
        else:
            plan["not_found"].append(e)

    for canonical in ("num_slots_per_segment", "shaft_height", "sleeve_thickness"):
        e = FPC.BY_CANONICAL[canonical]
        if up.itemByName(canonical) is not None:
            plan["already"].append(e)
            continue
        plan["create"].append(e)
        ops.append({"kind": "create", "canonical": canonical, "unit": e["unit"]})

    # stator_diameter <- stator_up_r: the one expression edit.
    sd_entry = FPC.BY_CANONICAL["stator_diameter"]
    stator_up_r = up.itemByName("stator_up_r")
    stator_diameter_exists = up.itemByName("stator_diameter") is not None
    if not stator_diameter_exists:
        plan["create"].append(sd_entry)
        ops.append({"kind": "create", "canonical": "stator_diameter", "unit": "mm",
                    "from_legacy": bool(stator_up_r)})
    if stator_up_r is not None:
        ops.append({"kind": "derive_stator_up_r"})

    # magnet_lamination <- mag_step (value only; mag_step untouched).
    ml_entry = FPC.BY_CANONICAL["magnet_lamination"]
    if up.itemByName("magnet_lamination") is None:
        plan["create"].append(ml_entry)
        ops.append({"kind": "create", "canonical": "magnet_lamination", "unit": "mm",
                    "from_legacy": bool(up.itemByName("mag_step"))})
    else:
        plan["already"].append(ml_entry)

    return plan, ops


def _plan_text(plan, defaults, defaults_note):
    def block(title, items, fmt):
        if not items:
            return ""
        return "\n\n%s (%d)\n%s" % (title, len(items),
                                     "\n".join("  " + fmt(e) for e in items))

    return (
        "motor_ai_sim: rename legacy Fusion parameters to canonical names\n"
        "(nothing is ever deleted; the only formula changed is stator_up_r's)\n"
        + block("WILL RENAME (Name only, values unaffected)", plan["rename"],
                lambda e: "%s -> %s" % (e["old"], e["canonical"]))
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

        # REPAIR DETECTION (2026-09-25).  Undo of the FIRST version of this
        # script (commit e65e16e), which plainly renamed stator_up_r ->
        # stator_diameter (keeping the RADIUS value) and mag_step ->
        # magnet_lamination (keeping the motor-length value). See
        # fusion_param_common.plan_v1_repair's docstring.
        repair = _plan_repair(up, um)
        hide, alias = set(), {}
        if repair["repair_stator_diameter"]:
            hide.add("stator_diameter")
            alias["stator_up_r"] = "stator_diameter"
        if repair["repair_magnet_lamination"]:
            hide.add("magnet_lamination")
            alias["mag_step"] = "magnet_lamination"
        view = _PostRepairView(up, hide, alias) if (hide or alias) else up

        # The normal v2 plan, computed against `view` -- i.e. as the design
        # will look AFTER the repair renames (if any) are applied.
        plan, ops = _plan(view, defaults)

        repair_ops = []
        if repair["repair_stator_diameter"]:
            repair_ops.append({"kind": "repair_rename", "old": "stator_diameter",
                                "canonical": "stator_up_r", "evidence": repair["stator_evidence"]})
        if repair["repair_magnet_lamination"]:
            repair_ops.append({"kind": "repair_rename", "old": "magnet_lamination",
                                "canonical": "mag_step", "evidence": []})
        all_ops = repair_ops + ops

        # GUARD: refuse outright if the plan contains anything outside the
        # allowed operation kinds (rename / create / the one expression
        # edit / the v1-bug repair rename) -- see the module docstring.
        bad = sorted({o["kind"] for o in all_ops} - ALLOWED_OP_KINDS)
        if bad:
            ui.messageBox("REFUSING TO RUN: the plan contains operation kind(s) %s, "
                           "outside the allowed rename/create/one-expression-edit/repair set. "
                           "Nothing was changed." % bad)
            return

        if not all_ops:
            ui.messageBox("Nothing to do: no legacy names found and every canonical "
                           "parameter already exists.\n" + _plan_text(plan, defaults, defaults_note))
            return

        plan_text = _plan_text(plan, defaults, defaults_note)
        if repair_ops:
            repair_lines = []
            for op in repair_ops:
                line = "  %s -> %s" % (op["old"], op["canonical"])
                if op["evidence"]:
                    line += "  [%s]" % "; ".join(op["evidence"])
                repair_lines.append(line)
            plan_text = ("REPAIR (undo of the first script version) (%d)\n%s\n\n"
                         "This undoes commit e65e16e's original, WRONG plain rename of these "
                         "two parameters (it kept the old radius/segment-length value under the "
                         "new canonical name); the normal plan below then runs on the "
                         "now-correctly-legacy-named design.\n"
                         % (len(repair_ops), "\n".join(repair_lines))) + plan_text

        answer = ui.messageBox(plan_text +
                                "\n\nApply these changes?", "motor_ai_sim: rename parameters",
                                adsk.core.MessageBoxButtonTypes.YesNoButtonType)
        if answer != adsk.core.DialogResults.DialogYes:
            ui.messageBox("Cancelled -- nothing changed.")
            return

        log = {"repaired": [], "renamed": [], "created": [], "derived": [], "failed": []}

        # 0) REPAIR renames first -- on the REAL `up` (not the view), so the
        #    plain-rename step right after this sees the truly-repaired
        #    design. A repair rename only changes the Name (never deletes,
        #    never edits the value) -- Fusion rewrites dependent formulas
        #    automatically, exactly as it did for the original wrong rename.
        for op in repair_ops:
            try:
                up.itemByName(op["old"]).name = op["canonical"]
                log["repaired"].append("%s -> %s" % (op["old"], op["canonical"]))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("repair %s: %s" % (op["old"], ex))

        # 1) plain renames (Fusion auto-updates every dependent reference;
        #    nothing else in the design is touched for these).
        for op in ops:
            if op["kind"] != "rename":
                continue
            try:
                up.itemByName(op["old"]).name = op["canonical"]
                log["renamed"].append("%s -> %s" % (op["old"], op["canonical"]))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("%s: %s" % (op["old"], ex))

        # 2) creates -- stator_diameter/magnet_lamination read their legacy
        #    source's CURRENT value first (still present, still untouched).
        stator_up_r = up.itemByName("stator_up_r")
        stator_up_r_val = _read_value(um, stator_up_r, "mm") if stator_up_r else None
        mag_step = up.itemByName("mag_step")
        mag_step_val = _read_value(um, mag_step, "mm") if mag_step else None
        motor_length_param = up.itemByName("motor_length") or up.itemByName("stator_w")
        motor_length_val = (_read_value(um, motor_length_param, "mm")
                             if motor_length_param else None)

        for op in ops:
            if op["kind"] != "create":
                continue
            canonical = op["canonical"]
            try:
                if canonical == "stator_diameter" and stator_up_r_val is not None:
                    val = FPC.convert_forward(FPC.BY_CANONICAL[canonical], stator_up_r_val)
                elif canonical == "magnet_lamination" and mag_step_val is not None and motor_length_val is not None:
                    val = FPC.convert_forward(FPC.BY_CANONICAL[canonical], mag_step_val,
                                               motor_length_value=motor_length_val)
                else:
                    val = defaults.get(canonical, 0.0)
                expr = _expr(val, op["unit"])
                up.add(canonical, adsk.core.ValueInput.createByString(expr), op["unit"],
                       "created by fusion360_rename_params.py (owner-approved 2026-09-25 mapping)")
                log["created"].append("%s = %s" % (canonical, expr))
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("%s: %s" % (canonical, ex))

        # 3) the one expression edit.
        for op in ops:
            if op["kind"] != "derive_stator_up_r":
                continue
            try:
                p = up.itemByName("stator_up_r")
                p.expression = FPC.derive_stator_up_r_expression()
                log["derived"].append("stator_up_r = \"%s\"" % p.expression)
            except Exception as ex:  # noqa: BLE001
                log["failed"].append("stator_up_r: %s" % ex)

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
    head = ("motor_ai_sim: legacy parameters renamed (nothing deleted)\n\n"
            "repaired (v1-bug undo): %d\nrenamed: %d\ncreated: %d\n"
            "derived (formula changed): %d\nfailed: %d"
            % (len(log["repaired"]), len(log["renamed"]), len(log["created"]),
               len(log["derived"]), len(log["failed"])))
    body = (_block("REPAIRED", log["repaired"])
            + _block("RENAMED", log["renamed"])
            + _block("CREATED", log["created"])
            + _block("DERIVED", log["derived"])
            + _block("FAILED", log["failed"]))
    return head + body
