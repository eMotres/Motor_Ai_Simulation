"""ONE parameter source for the CAD models: the simulation die/config store -> FreeCAD motor profile.

The store (``<dies>/<die>/{die.yaml, L*.yaml}`` + the materials library) is authoritative.  This module only READS it
(stdlib + PyYAML, no motor_ai_sim imports, so it runs in the FreeCAD image too):

  load_effective(dies_dir, die, config, materials_library)  die geometry + config overrides + materials + winding
  load_mapping(root)                                          simulation key -> CAD parameter name (same mapping as
                                                              GET /api/fusion/params.csv: config/fusion_param_map.yaml,
                                                              DERIVED_GEOMETRY_NAMES, FUSION_EXCLUDED_NAMES, geometry_schema)
  make_profile(eff, mapping, base_profile, ...)               the FreeCAD profile JSON the template builds from
  geo_sig(geometry)                                           the machine's ``_geoSig`` (routes.presets._geo_sig dialect)
  back(...)                                                   the owner's edited Params -> diff + proposal (never applied)

History: moved here from CIANO14_40_motor_model/tools/sim_sync.py (2026-10-12, commit 8ef2e35 of that repo).
"""
import ast
import copy
import csv
import datetime
import hashlib
import io
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))          # repo root (or /app in the images)
CAD_DIR = os.path.join(ROOT, "cad")
BASE_PROFILE = os.path.join(CAD_DIR, "reference", "CIANO14_40_12.json")  # Fusion reference profile (open Fusion model)
FUSION_VALUES = os.path.join(CAD_DIR, "reference", "user_parameters_values_2026-10-12.json")
DEFAULT_SERVER_DIES = "/srv/motres/shared/dies"
DEFAULT_SERVER_MATERIALS = "/srv/motres/shared/materials_library.yaml"

# simulation material slot -> FreeCAD body
MAT_BODIES = {"magnet": "Magnet", "stator_core": "Core", "rotor_core": "RotorCore", "slot": "Coil"}
# simulation derived value -> equivalent FreeCAD expression (cross-check only)
DERIVED_EQ = {"stator_outer_radius": "stator_up_r", "stator_inner_radius": "stator_down_r",
              "rotor_outer_radius": "rotor_up_r", "rotor_inner_radius": "rotor_down_r - rotor_house_height",
              "angle_slot": "angle_wire", "angle_pole": "angle_mag", "num_slots": "num_seg * 6",
              "num_poles": "num_seg * num_poles_per_segment"}
NO_CAD = {"wire_split": "strips across the wire width (CAD draws one strip; template requires 1)",
          "wire_parallel": "parallel wires per turn - electrical only, no CAD geometry"}
FUSION_MATS = {"Core": "20SW1200", "Coil": "Copper", "RotorCore": "Steel", "Magnet": "Steel (placeholder)"}


def default_dies_dir():
    return os.environ.get("MOTRES_DIES_DIR") or (DEFAULT_SERVER_DIES if os.path.isdir(DEFAULT_SERVER_DIES)
                                                 else os.path.join(ROOT, "config", "dies"))


def default_materials_library():
    return os.environ.get("MOTRES_MATERIALS_LIBRARY") or (
        DEFAULT_SERVER_MATERIALS if os.path.exists(DEFAULT_SERVER_MATERIALS)
        else os.path.join(ROOT, "config", "materials_library.yaml"))


def slug(text):
    """Die / config name -> a file-name safe id: 'CIANO14 40 new' -> 'CIANO14_40_new'."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")


def config_stem(config):
    c = str(config)
    return c[:-5] if c.lower().endswith(".yaml") else c


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------- expression evaluator
_UNIT = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "um": 1e-3, "deg": 1.0, "rad": 180.0 / math.pi, "in": 25.4}
_NUM_UNIT = re.compile(r"(\d+\.?\d*(?:[eE][-+]?\d+)?)\s*(mm|cm|um|deg|rad|in|m)\b")
_FUN = dict(pi=math.pi, sqrt=math.sqrt, abs=abs, min=min, max=max, sin=lambda d: math.sin(math.radians(d)),
            cos=lambda d: math.cos(math.radians(d)), tan=lambda d: math.tan(math.radians(d)))


def evaluate(exprs):
    """{name: FreeCAD/Fusion expression} -> {name: value} (lengths mm, angles deg).  Raises on unknown names/loops."""
    vals, busy = {}, set()

    def ev(n):
        if n in vals:
            return vals[n]
        if n in busy:
            raise ValueError("circular expression at %s" % n)
        busy.add(n)
        e = str(exprs[n]).strip().lstrip("=")
        e = _NUM_UNIT.sub(lambda m: "(%s*%r)" % (m.group(1), _UNIT[m.group(2)]), e)
        e = re.sub(r"\b(mm|deg)\b", "1", e).replace("^", "**")
        ns = dict(_FUN)
        for tok in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", e)):
            if tok in exprs and tok not in ns:
                ns[tok] = ev(tok)
        v = float(eval(e, {"__builtins__": {}}, ns))
        busy.discard(n)
        vals[n] = v
        return v

    for k in exprs:
        ev(k)
    return vals


def _eq(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _fmt(v, unit):
    return ("%g mm" % v) if unit == "mm" else ("%g" % v)


# ----------------------------------------------------------------------------------------------- mapping
def _ast_set(path, name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        tgt = getattr(node, "targets", None) or ([node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == name for t in tgt):
            v = node.value
            if isinstance(v, ast.Call) and v.args:
                v = v.args[0]
            return sorted(ast.literal_eval(v))
    raise KeyError("%s not found in %s" % (name, path))


def load_mapping(root=None):
    """Name mapping reused from the simulation, not invented (parsed, not imported: no heavy dependencies)."""
    import yaml
    root = root or ROOT
    mp_file = os.path.join(root, "config", "fusion_param_map.yaml")
    m = (yaml.safe_load(open(mp_file, encoding="utf-8")) or {}).get("map") or {}
    cfg_file = os.path.join(root, "config", "motor_config.yaml")
    sch = (yaml.safe_load(open(cfg_file, encoding="utf-8")) or {}).get("geometry_schema") or {}
    vfile = os.path.join(root, "src", "motor_ai_sim", "routes", "_validation.py")
    ffile = os.path.join(root, "src", "motor_ai_sim", "routes", "fusion.py")
    return dict(map={str(k): str(v) for k, v in m.items()},
                schema={k: dict(unit=str(v.get("unit", "") or ""), description=str(v.get("description", "") or ""))
                        for k, v in sch.items()},
                derived=_ast_set(vfile, "DERIVED_GEOMETRY_NAMES"), fusion_excluded=_ast_set(ffile, "FUSION_EXCLUDED_NAMES"),
                sources={os.path.relpath(p, root).replace("\\", "/"): sha256_file(p)
                         for p in (mp_file, cfg_file, vfile, ffile)})


def primary_rows(geo, mp):
    """(sim key, CAD parameter name, value, unit, description) - the rows of GET /api/fusion/params.csv."""
    out = []
    for k in sorted(geo):
        v = geo[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if k in mp["derived"] or k in mp["fusion_excluded"]:
            continue
        sc = mp["schema"].get(k, {})
        out.append((k, mp["map"].get(k, k), float(v), "mm" if sc.get("unit") == "mm" else "", sc.get("description", "")))
    return out


def fusion_csv(rows, path):
    """Parameter I/O CSV exactly like motor_ai_sim routes/fusion.py export_params_csv (six columns, utf-8-sig)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(["Name", "Unit", "Expression", "Value", "Comment", "Favorite"])
    for key, fname, v, unit, desc in rows:
        comment = (("motor_ai_sim:" + key + " ") if fname != key else "") + desc
        num = "%g" % v
        w.writerow([fname, unit, num, num, comment.strip(), "False"])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue().encode("utf-8-sig"))


# ----------------------------------------------------------------------------------------------- fingerprints
def _fmt_num(v):
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def geo_sig(geometry):
    """The machine's ``_geoSig``: ``key:value|...`` over the numeric geometry fields sorted by key - the SAME
    construction as routes.presets._geo_sig and the frontend's geoSignature (a test pins the equality)."""
    return "|".join("%s:%s" % (k, _fmt_num(v)) for k, v in sorted((geometry or {}).items())
                    if isinstance(v, (int, float)) and not isinstance(v, bool))


def geo_sig_hash(sig):
    """Short id of a ``_geoSig`` (report._geo_sig_hash: sha1, 10 hex)."""
    return hashlib.sha1(str(sig).encode("utf-8")).hexdigest()[:10] if sig else None


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------------------------- the store
def list_configs(dies_dir, die):
    d = os.path.join(dies_dir, die)
    return sorted(f[:-5] for f in os.listdir(d) if f.lower().endswith(".yaml") and f != "die.yaml")


def load_effective(dies_dir, die, config, materials_library=None):
    """Effective machine of <die>/<config>: die geometry updated by the config's geometry_overrides, the config's
    materials (cards from the library) and winding.  Every input file is hashed."""
    import yaml
    cfg_name = config_stem(config) + ".yaml"
    ddir = os.path.join(dies_dir, die)
    die_file, cfg_file = os.path.join(ddir, "die.yaml"), os.path.join(ddir, cfg_name)
    for p in (die_file, cfg_file):
        if not os.path.isfile(p):
            raise FileNotFoundError("%s not found (die %r, config %r; configs of the die: %s)" % (
                p, die, config, ", ".join(list_configs(dies_dir, die)) if os.path.isdir(ddir) else "die directory missing"))
    dy = yaml.safe_load(open(die_file, encoding="utf-8")) or {}
    cy = yaml.safe_load(open(cfg_file, encoding="utf-8")) or {}
    geo = dict(dy.get("geometry") or {})
    geo.update(cy.get("geometry_overrides") or {})
    files = {}
    for p in (die_file, cfg_file):
        st = os.stat(p)
        files[os.path.basename(p)] = dict(sha256=sha256_file(p), size=st.st_size,
                                          mtime=datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"))
    cards = {}
    lib_path = materials_library or default_materials_library()
    mats = cy.get("materials") or {}
    if mats and os.path.exists(lib_path):
        lib = yaml.safe_load(open(lib_path, encoding="utf-8")) or {}
        for slot, card in mats.items():
            for cat, d in lib.items():
                if isinstance(d, dict) and card in d:
                    cards[card] = dict(category=cat, **{k: x for k, x in d[card].items() if k != "bh_curve"})
        files["materials_library.yaml"] = dict(sha256=sha256_file(lib_path), size=os.path.getsize(lib_path),
                                               mtime=datetime.datetime.fromtimestamp(os.path.getmtime(lib_path)).isoformat(timespec="seconds"),
                                               cards_used=sorted(cards))
    return dict(die=die, config=cfg_name, variant=config_stem(config), geometry=geo,
                geometry_overrides=cy.get("geometry_overrides") or {}, die_geometry=dy.get("geometry") or {},
                materials=mats, winding=cy.get("winding") or {}, bearings=cy.get("bearings") or {},
                material_cards=cards, files=files, dies_dir=dies_dir)


def parameter_fingerprint(eff, mp):
    """What the CAD depends on: the primary geometry rows + materials + winding.  Same store values -> same hash."""
    rows = {k: v for k, _f, v, _u, _d in primary_rows(eff["geometry"], mp)}
    return dict(geo_sig=geo_sig(eff["geometry"]), geo_sig_hash=geo_sig_hash(geo_sig(eff["geometry"])),
                cad_param_hash=canonical_hash(dict(rows=rows, materials=eff["materials"], winding=eff["winding"])))


# ----------------------------------------------------------------------------------------------- profile (pull)
def make_profile(eff, mp, base_profile=None, fusion_values=None, stamp=None, motor_id=None):
    """Store -> (profile dict for the FreeCAD template, review report).  Where a formula of the Fusion reference
    profile already gives the simulation value it is kept (Fusion intent); any other value becomes a constant."""
    base = json.load(open(base_profile or BASE_PROFILE, encoding="utf-8"))
    fus = {}
    fv = fusion_values or FUSION_VALUES
    if fv and os.path.exists(fv):
        fus = {p["name"]: p["value"] for p in json.load(open(fv, encoding="utf-8"))}
    pj = copy.deepcopy(base)
    params = {p["name"]: p for sec in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part")
              for p in pj.get(sec, [])}
    geo = eff["geometry"]
    rows = primary_rows(geo, mp)
    report = []
    before = evaluate({n: p["freecad_expression"] for n, p in params.items()})
    for key, fname, v, unit, desc in rows:
        p = params.get(fname)
        row = dict(sim_key=key, name=fname, sim=v, fusion=fus.get(fname), unit=unit,
                   sim_file=eff["config"] if key in eff["geometry_overrides"] else "die.yaml")
        if p is None:
            row.update(freecad=None, status="sim only - " + NO_CAD.get(key, "no CAD parameter"))
        else:
            row["freecad_before"] = round(before[fname], 9)
            if not _eq(before[fname], v):
                row["expression_before"] = p["freecad_expression"]
                p["freecad_expression"] = _fmt(v, unit)
            p["source"] = "simulation %s/%s:%s" % (eff["die"], row["sim_file"], key)
        report.append(row)
    vals = evaluate({n: p["freecad_expression"] for n, p in params.items()})
    for n, p in params.items():
        p["value"] = round(vals[n], 9)
    for row in report:
        if row["name"] in params:
            row["freecad"] = round(vals[row["name"]], 9)
            sim_ok = _eq(row["freecad"], row["sim"])
            fus_ok = _eq(row["fusion"], row["sim"]) if row["fusion"] is not None else None
            row["status"] = ("ok" if sim_ok else "FreeCAD != sim") + ("" if fus_ok in (True, None) else
                                                                      "; Fusion open model differs (import the CSV)")
    derived = []
    for k, e in DERIVED_EQ.items():
        if k in geo:
            fvv = evaluate(dict({n: p["freecad_expression"] for n, p in params.items()}, _x=e))["_x"]
            derived.append(dict(sim_key=k, freecad_expression=e, sim=geo[k], freecad=round(fvv, 9),
                                status="ok" if _eq(fvv, geo[k], 1e-9) else "DIFFERENT"))
    excluded = [dict(sim_key=k, sim=geo.get(k), freecad=(params[k]["value"] if k in params else None),
                     note="excluded from the Fusion export by motor_ai_sim (FUSION_EXCLUDED_NAMES); not synced")
                for k in mp["fusion_excluded"] if k in geo]
    mats = dict(pj["profile"].get("materials", {}))
    mat_rows = []
    for slot, body in MAT_BODIES.items():
        card = eff["materials"].get(slot)
        if card:
            mat_rows.append(dict(body=body, sim_slot=slot, sim_card=card, before=mats.get(body),
                                 fusion=FUSION_MATS.get(body)))
            mats[body] = card
    prof = pj["profile"]
    mid = motor_id or "%s_%s" % (slug(eff["die"]), eff["variant"])
    stamp = stamp or datetime.datetime.now().isoformat(timespec="seconds")
    fp = parameter_fingerprint(eff, mp)
    prof.update(motor_id=mid, profile_file=mid, doc_name=mid,
                variant="%s (simulation die %s, config %s)" % (eff["variant"], eff["die"], eff["config"]),
                revision="B.0", status="parameters from the simulation store; geometry template validated against Fusion",
                materials=mats, material_cards={c: eff["material_cards"][c] for c in eff["materials"].values()
                                                if c in eff["material_cards"]},
                winding=eff["winding"], generated=stamp)
    prof["source"] = dict(prof.get("source", {}), simulation=dict(
        die=eff["die"], config=eff["config"], store=eff.get("dies_dir"), files=eff["files"], fingerprint=fp,
        mapping="motor_ai_sim config/fusion_param_map.yaml + DERIVED_GEOMETRY_NAMES + FUSION_EXCLUDED_NAMES",
        rule="simulation store is authoritative (owner 2026-10-12); Fusion = template geometry reference"))
    pj.setdefault("history", []).append(dict(date=stamp, action="motor_ai_sim.cad.store.make_profile %s/%s" % (eff["die"], eff["variant"]),
                                             changes=[dict(name=r["name"], old=r.get("freecad_before"), new=r.get("freecad"))
                                                      for r in report if r.get("freecad_before") is not None
                                                      and not _eq(r["freecad_before"], r["freecad"])]))
    res = dict(die=eff["die"], variant=eff["variant"], motor_id=mid, generated=stamp, fingerprint=fp,
               parameters=report, derived_cross_check=derived, excluded=excluded, materials=mat_rows,
               summary=dict(n=len(report),
                            freecad_equals_sim=all(r["status"].startswith("ok") for r in report if r.get("freecad") is not None),
                            changed_vs_fusion_reference=[r["name"] for r in report if r.get("freecad_before") is not None
                                                         and not _eq(r["freecad_before"], r["freecad"])],
                            fusion_differs_from_sim=[r["name"] for r in report if r.get("fusion") is not None
                                                     and not _eq(r["fusion"], r["sim"])],
                            sim_only=[r["name"] for r in report if r.get("freecad") is None],
                            derived_all_equal=all(d["status"] == "ok" for d in derived)))
    return pj, res


# ----------------------------------------------------------------------------------------------- back (owner edits)
def read_fcstd_params(path, sheet="Params"):
    """{alias: expression} of the Params spreadsheet of an FCStd (zip / Document.xml), no FreeCAD needed."""
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Document.xml"))
    for obj in root.iter("Object"):
        if obj.get("name") != sheet:
            continue
        for prop in obj.iter("Property"):
            if prop.get("name") == "cells":
                return {c.get("alias"): (c.get("content") or "").lstrip("=") for c in prop.iter("Cell") if c.get("alias")}
    raise KeyError("no %s sheet in %s" % (sheet, path))


def back(eff, mp, cells, out_dir, propose=False, origin="?", base_profile=None):
    """cells = {alias: expression} of the owner's Params.  Writes the review files into out_dir (never the store) and
    returns the result.  With propose also a Fusion CSV and a patch YAML (NOT applied anywhere)."""
    vals = evaluate(cells)
    geo = eff["geometry"]
    rows = primary_rows(geo, mp)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    diff, same, missing = [], 0, []
    configs = list_configs(eff["dies_dir"], eff["die"]) if os.path.isdir(os.path.join(eff["dies_dir"], eff["die"])) else []
    for key, fname, v, unit, desc in rows:
        if fname not in vals:
            missing.append(key)
            continue
        fv = vals[fname]
        if _eq(fv, v):
            same += 1
            continue
        diff.append(dict(sim_key=key, name=fname, sim=v, freecad=round(fv, 9), delta=round(fv - v, 9), unit=unit,
                         expression=cells[fname],
                         target=("%s geometry_overrides" % eff["config"]) if key in eff["geometry_overrides"]
                         else "die.yaml geometry (affects every config of the die: %s)" % ", ".join(configs)))
    base = {p["name"]: p for sec in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part")
            for p in json.load(open(base_profile or BASE_PROFILE, encoding="utf-8")).get(sec, [])}
    mapped = {mp["map"].get(k, k) for k in geo}
    cad_only = [dict(name=n, freecad=round(vals[n], 9), role=base.get(n, {}).get("role", "added by owner"),
                     expression=cells[n]) for n in sorted(vals) if n not in mapped and n in cells
                and base.get(n, {}).get("role") in ("input", None)]
    res = dict(die=eff["die"], variant=eff["variant"], origin=origin, generated=stamp, store_files=eff["files"],
               compared=len(rows), equal=same, differences=diff, not_in_freecad=missing, cad_only_inputs=cad_only,
               note="read-only: nothing was written to the simulation store; a proposal (if any) is applied by the "
                    "orchestrator after the owner confirms it")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, "%s_back_%s" % (eff["variant"], stamp))
    with open(stem + ".csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["parameter", "sim_key", "simulation", "freecad", "delta", "unit", "freecad_expression", "where in the simulation"])
        for d in diff:
            w.writerow([d["name"], d["sim_key"], "%g" % d["sim"], "%g" % d["freecad"], "%g" % d["delta"], d["unit"],
                        d["expression"], d["target"]])
    res["files"] = [stem + ".json", stem + ".csv"]
    if propose and diff:
        prow = [(k, f, (vals[f] if f in vals else v), u, d) for k, f, v, u, d in rows]
        pstem = os.path.join(out_dir, "%s_proposal_%s" % (eff["variant"], stamp))
        fusion_csv(prow, pstem + "_fusion_params.csv")
        ddir = os.path.join(eff["dies_dir"], eff["die"])
        L = ["# PROPOSAL ONLY - not applied.  Generated by motor_ai_sim.cad (back --propose) from %s (%s)." % (origin, stamp),
             "# The orchestrator applies it to the store after the owner confirms; nothing here writes the store.",
             "die: %s" % json.dumps(eff["die"]), "variant: %s" % eff["variant"], "changes:"]
        by_file = {}
        for d in diff:
            f = (ddir + "/" + eff["config"], "geometry_overrides") if d["sim_key"] in eff["geometry_overrides"] \
                else (ddir + "/die.yaml", "geometry")
            by_file.setdefault(f, []).append(d)
        for (fp, sec), ds in by_file.items():
            L += ["  - file: %s" % json.dumps(fp), "    section: %s" % sec, "    set:"]
            for d in ds:
                L.append("      %s: %s   # was %s (FreeCAD expression: %s)" % (d["sim_key"], "%g" % d["freecad"], "%g" % d["sim"],
                                                                             d["expression"]))
        if any(sec == "geometry" for (_, sec) in by_file):
            L.append("warnings:\n  - die.yaml changes affect every configuration of the die (%s)" % ", ".join(configs))
        with open(pstem + ".patch.yaml", "w", encoding="utf-8") as fh:
            fh.write("\n".join(L) + "\n")
        res["files"] += [pstem + "_fusion_params.csv", pstem + ".patch.yaml"]
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    return res
