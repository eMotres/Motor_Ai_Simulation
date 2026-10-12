"""Params spreadsheet <-> motor profile JSON (params/<motor>.json).

Sheet `Params` (owner-editable, like Fusion user parameters):
  A: name   B: value (=expression, alias = name)   C: unit   D: role   E: comment / source
Row colour = role:  input (green) and derived (pale green) drive the geometry; mechanics (blue) drives the mechanical
parts (shaft, bearings, holders, rings, Prop_fix, screws) and has no simulation counterpart; info (grey) is reference only; unsupported (orange) is ignored by this
template (pre-check demands its nominal value); foreign (dark grey) belongs to another design in the Fusion file.

Round trip (M01): read_sheet() -> apply_sheet_to_profile() writes the owner's edits (expressions AND values, by
alias) back into the profile JSON (tools/sync_params.py); build_document() always builds from the JSON.
"""
import copy
import datetime
import json

ROLE_COLOURS = {
    "input": (0.80, 0.94, 0.80), "derived": (0.90, 0.97, 0.90), "mechanics": (0.82, 0.88, 0.98),
    "info": (0.90, 0.90, 0.90), "unsupported": (1.00, 0.82, 0.60), "foreign": (0.75, 0.75, 0.75),
}
LIVE_ROLES = ("input", "derived", "mechanics")
SECTIONS = ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part", "named_parameters_mechanics")
SECTION_TITLES = {
    "user_parameters": "Fusion user parameters",
    "named_parameters_sketch3": "Axial layout - named fixed numbers of Fusion Sketch3 (mechanics)",
    "named_parameters_active_part": "Active part - fixed numbers inside Fusion features",
    "named_parameters_mechanics": "Mechanics - bearing card, numbers of the Fusion mechanical features, axial chain (stage 3)",
}


def load_params_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_params_json(pj, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pj, fh, indent=1, ensure_ascii=False)


def all_params(pj):
    for sec in SECTIONS:
        for p in pj.get(sec, []):          # mechanical / active-part sections are optional (N02)
            yield sec, p


def apply_overrides(pj, overrides):
    """overrides: {name: expression} -> new profile dict (expressions only; values are re-read after recompute)."""
    pj = copy.deepcopy(pj)
    names = {p["name"]: p for _, p in all_params(pj)}
    for k, ex in overrides.items():
        if k not in names:
            raise KeyError("unknown parameter %s" % k)
        names[k]["freecad_expression"] = ex
    return pj


def parse_overrides(text):
    """'a=1 mm;b=3' -> {'a': '1 mm', 'b': '3'}"""
    out = {}
    for item in (text or "").split(";"):
        if item.strip():
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _rows(pj):
    prof = pj.get("profile", {})
    for sec in SECTIONS:
        lst = pj.get(sec, [])
        if not lst:
            continue
        title = SECTION_TITLES[sec]
        if sec == "user_parameters":
            title += " (%s, %s)" % (prof.get("source", {}).get("fusion_document", "?"), prof.get("source", {}).get("read", "?"))
        yield ("#", title, "", "", "")
        for p in lst:
            com = p.get("comment", "")
            if p.get("source") and sec != "user_parameters":
                com = "%s | %s" % (p["source"], com)
            if p.get("role_note"):
                com = ("[%s] %s" % (p["role_note"], com)).strip()
            yield (p["name"], p["freecad_expression"], p.get("unit", ""), p.get("role", "input"), com)


def make_params_sheet(doc, pj, name="Params"):
    import FreeCAD as App  # noqa: F401
    sh = doc.getObject(name)
    if sh is None:
        sh = doc.addObject("Spreadsheet::Sheet", name)
    for c, t in zip("ABCDE", ("name", "value", "unit", "role", "comment / source")):
        sh.set("%s1" % c, t)
    prof = pj.get("profile", {})
    r = 2
    sh.set("A%d" % r, "'%s  variant %s  revision %s  (profile JSON: params/%s.json)" % (
        prof.get("motor_id", "?"), prof.get("variant", "?"), prof.get("revision", "?"), prof.get("profile_file", prof.get("motor_id", "?"))))
    r += 1
    rows = list(_rows(pj))
    # pass 1: every alias first (a formula may use a row defined further down; FreeCAD binds names when the cell is set)
    rr = r
    for nm, ex, unit, role, com in rows:
        if nm != "#":
            sh.set("B%d" % rr, "0")
            sh.setAlias("B%d" % rr, nm)
        rr += 1
    for nm, ex, unit, role, com in rows:
        if nm == "#":
            sh.set("A%d" % r, "'" + ex)
        else:
            sh.set("A%d" % r, "'" + nm)
            sh.set("B%d" % r, "=" + ex)
            sh.set("C%d" % r, "'" + unit)
            sh.set("D%d" % r, "'" + role)
            if com:
                sh.set("E%d" % r, "'" + com)
            col = ROLE_COLOURS.get(role)
            if col:
                try:
                    sh.setBackground("A%d:D%d" % (r, r), col)
                except Exception:
                    pass
        r += 1
    sh.setColumnWidth("A", 190); sh.setColumnWidth("B", 110); sh.setColumnWidth("D", 80); sh.setColumnWidth("E", 560)
    return sh


def pval(sh, name):
    """Numeric value of a Params cell (mm for lengths, deg for angles)."""
    v = sh.get(name)
    return float(v.Value) if hasattr(v, "Value") else float(v)


def set_param(sh, name, expr):
    """Change a parameter like the owner would (cell keeps its alias)."""
    cell = sh.getCellFromAlias(name)
    if not cell:
        raise KeyError("no Params cell with alias %s" % name)
    sh.set(cell, "=" + str(expr))


def read_sheet(sh):
    """{alias: dict(expression, value)} for every aliased cell of the sheet."""
    out = {}
    for cell in sh.getUsedCells():
        alias = sh.getAlias(cell)
        if not alias:
            continue
        content = sh.getContents(cell)
        expr = content[1:] if content.startswith("=") else content
        out[alias] = dict(expression=expr.strip(), value=pval(sh, alias))
    return out


def sheet_differs(pj, sheet, canon=None):
    """Names whose expression differs between profile JSON and sheet (+ names only in one of them).
    canon: {name: expression as FreeCAD normalises it} for the JSON side (FreeCAD drops redundant brackets and
    trailing zeros, so the raw JSON text must not be compared with the sheet text)."""
    js = {p["name"]: (canon or {}).get(p["name"], p["freecad_expression"]) for _, p in all_params(pj)}
    diff = sorted(k for k in js if k in sheet and _norm(js[k]) != _norm(sheet[k]["expression"]))
    only_js = sorted(set(js) - set(sheet))
    only_sh = sorted(set(sheet) - set(js))
    return diff, only_js, only_sh


def _norm(e):
    return " ".join(str(e).replace("(", " ( ").replace(")", " ) ").split())


def apply_sheet_to_profile(pj, sheet, note="sync from FCStd", canon=None):
    """Return (new profile, changes): expressions and values from the sheet, by alias."""
    pj = copy.deepcopy(pj)
    changes = []
    for _, p in all_params(pj):
        s = sheet.get(p["name"])
        if s is None:
            continue
        if _norm(s["expression"]) != _norm((canon or {}).get(p["name"], p["freecad_expression"])):
            changes.append(dict(name=p["name"], old=p["freecad_expression"], new=s["expression"]))
            p["freecad_expression"] = s["expression"]
        p["value"] = round(s["value"], 9)
    known = {p["name"] for _, p in all_params(pj)}
    added = [dict(name=k, freecad_expression=v["expression"], value=round(v["value"], 9), unit="", role="input",
                  comment="added by the owner in the FreeCAD Params sheet") for k, v in sheet.items() if k not in known]
    if added:
        pj.setdefault("named_parameters_active_part", []).extend(added)
        changes += [dict(name=a["name"], old=None, new=a["freecad_expression"]) for a in added]
    pj.setdefault("history", []).append(dict(date=datetime.datetime.now().isoformat(timespec="seconds"), action=note,
                                             changes=changes))
    return pj, changes


def compare_with_json(sh, pj, tol=1e-6):
    bad = []
    for _, p in all_params(pj):
        v = pval(sh, p["name"])
        if abs(v - p["value"]) > tol * max(1.0, abs(p["value"])):
            bad.append((p["name"], v, p["value"]))
    return bad


def live_parameters(doc, sheet_name="Params"):
    """Aliases that actually drive geometry: referenced by expressions of model objects, plus their sheet inputs."""
    import re
    sh = doc.getObject(sheet_name)
    sheet = read_sheet(sh)
    names = set(sheet)
    direct = set()
    for o in doc.Objects:
        if o is sh:
            continue
        for _, ex in getattr(o, "ExpressionEngine", []):
            for m in re.findall(r"Params\.([A-Za-z_][A-Za-z0-9_]*)", ex):
                direct.add(m)
    live = set(direct)
    stack = list(direct)
    while stack:
        n = stack.pop()
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sheet.get(n, {}).get("expression", "")):
            if tok in names and tok not in live:
                live.add(tok); stack.append(tok)
    return direct, live
