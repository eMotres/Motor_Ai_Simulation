# Fusion 360 SCRIPT (runs INSIDE Fusion: Utilities → Scripts and Add-Ins →
# "+" → this file).  One click: pull the live geometry of motor_ai_sim from the
# local API and write it into this design's User Parameters — existing ones
# updated, missing ones created — then let the model rebuild.
#
# The same values are available as a Parameter I/O CSV from
# http://localhost:8001/api/fusion/params.csv when the script route is not
# wanted.  Nothing is sent back to motor_ai_sim from here; the reverse
# direction is Parameter I/O → Export → "Import Fusion CSV" in the app, which
# reports every change before it lands.
#
# UNITS (2026-09-14).  A Fusion user parameter is either a LENGTH parameter
# (unit "mm") or a UNITLESS one (unit "", the counts and the ratios).  The two
# are not interchangeable and the API is blunt about it:
#   * UnitsManager.convert(v, "", "") raises "3 : Bad units parameter" — a
#     unitless quantity has no unit to convert between, so the comparison has
#     to read Parameter.value directly (it IS the number);
#   * a unitless parameter's expression must be a BARE number — assigning
#     "0.13 mm" raises "3 : Expression is invalid";
#   * .unit is only assigned when the model's unit DIFFERS from ours, and a
#     refusal there is reported as a unit mismatch the user has to resolve in
#     Fusion, not as a raw API error.
# That is the whole of the 2026-09-14 run's 8 failures: the 7 unitless rows
# died in the convert(), slot_hs died on "0.13 mm" written into a parameter the
# model keeps unitless.
#
# COMMENTS (2026-09-14).  The model's user parameters mostly have an empty
# Comment, so every parameter also gets OUR schema description: written on
# creation as before, and written onto an existing parameter whenever the
# model's comment is empty or says something else — including a parameter whose
# VALUE is unchanged.  A comment-only change is counted on its own line
# ("comment set") and is never reported as a value update.
#
# Replaces fusion360_controller.py (2026-02): that one carried eleven names
# the geometry no longer has.
import json
import traceback
import urllib.request

import adsk.core
import adsk.fusion

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

    Parameter.value is in DATABASE units — cm for a length, the plain number
    for a unitless parameter.  Only the length case goes through the units
    manager; convert() with an empty unit string is the "Bad units parameter"
    error, not a no-op.
    """
    try:
        if not unit:
            return float(existing.value)
        return float(um.convert(existing.value, um.internalUnits, unit))
    except Exception:      # noqa: BLE001 — an unreadable value just means "not comparable"
        return None


def _clip(text, n=48):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _write_value(um, existing, name, value, unit, expr):
    """Write the VALUE of an existing parameter.  ('same'|'updated'|'failed', detail)."""
    try:
        cur_unit = str(existing.unit or "")
    except Exception:      # noqa: BLE001
        cur_unit = unit    # unreadable -> assume ours, let the setters decide

    if cur_unit == unit:
        # Same unit on both sides: never touch .unit (assigning it on a
        # unitless parameter is the "Bad units parameter" error), compare in
        # the parameter's own unit so an unchanged 36 mm is not rewritten and
        # the timeline stays clean, and write only the expression.
        old = _current(um, existing, unit)
        if old is not None and abs(old - value) <= TOL:
            return "same", name
        existing.expression = expr
        return "updated", "%s: %s → %s" % (name, _num(old) if old is not None else "?", expr)

    # Different unit in the model.  The unit has to move FIRST — an expression
    # carrying a unit is invalid while the parameter is unitless — and if
    # Fusion refuses, leave the parameter exactly as found and say what has to
    # be changed by hand.
    old_raw = _current(um, existing, cur_unit)
    mismatch = ("%s: unit mismatch: model %r vs ours %r — change the unit in Fusion"
                % (name, cur_unit, unit))
    try:
        existing.unit = unit
    except Exception:      # noqa: BLE001
        return "failed", mismatch
    try:
        existing.expression = expr
    except Exception:      # noqa: BLE001
        try:
            existing.unit = cur_unit        # put it back as found
        except Exception:      # noqa: BLE001
            pass
        return "failed", mismatch
    return "updated", ("%s: %s → %s"
                       % (name, ("%s %s" % (_num(old_raw), cur_unit)).strip()
                          if old_raw is not None else "?", expr))


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Open a Fusion design first.")
            return
        with urllib.request.urlopen(API, timeout=10) as r:
            payload = json.load(r)
        params = payload.get("parameters") or []
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
                # 2026-09-14 — dirtying the timeline for that is accepted).  A
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
            except Exception as e:      # noqa: BLE001 — one bad row must not stop the rest
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
        shown.append("  … and %d more" % (len(lines) - limit))
    return "\n\n%s\n%s" % (title, "\n".join(shown))


def _summary(payload, updated, created, same, failed, noted):
    head = ("motor_ai_sim → Fusion parameters (%s)\n\n"
            "updated: %d\ncreated: %d\ncomment set: %d\nunchanged: %d\nfailed: %d"
            % (payload.get("machine", "?"), len(updated), len(created),
               len(noted), len(same), len(failed)))
    body = (_block("UPDATED (old → new)", updated)
            + _block("CREATED", created)
            + _block("COMMENT SET", noted)
            + _block("FAILED", failed))
    if same:
        body += "\n\nUNCHANGED\n  " + ", ".join(same)
    return head + body
