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
import operator
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))          # repo root (or /app in the images)
CAD_DIR = os.path.join(ROOT, "cad")
BASE_PROFILE = os.path.join(CAD_DIR, "reference", "CIANO14_40_12.json")  # Fusion reference profile (open Fusion model)
FUSION_VALUES = os.path.join(CAD_DIR, "reference", "user_parameters_values_2026-10-12.json")
DEFAULT_SERVER_DIES = "/srv/motres/shared/dies"
DEFAULT_SERVER_MATERIALS = "/srv/motres/shared/materials_library.yaml"
DEFAULT_SERVER_BEARINGS = "/srv/motres/shared/bearings_library.yaml"
SECTIONS = ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part", "named_parameters_mechanics")
# bearing card field -> CAD parameter (stage 3: the 618/8 seats and the bearing bodies follow the card of the config)
BEARING_ROWS = {"d": "bearing_d", "D": "bearing_D", "B": "bearing_B"}
# standard 618/8 dimensions (ISO 15 / SKF catalogue), used only when the store has no bearing card
BEARING_618_8 = dict(d=8, D=16, B=4, d_m=12)

# CAD-ONLY ASSEMBLY CLEARANCES (owner 2026-10-14).  The 3-D model carries small offsets so that a magnet (and the other parts)
# can surely be inserted at our manufacturing tolerances: ``offset_rotor`` widens the magnet pocket (the pocket fillet is
# ``magnet_fill_radius + offset_rotor``), the others are the matching fit clearances of the core, the teeth, the bearing seats,
# the radiator and the holders.  The simulation deliberately does NOT include them - they make no practical difference to
# the results.  They are therefore never a mismatch, never listed as a CAD-only input to adopt, and never proposed back to the
# simulation; masses use the nominal magnet (the clearance is air).  Private rule: motor-ai-sim-private docs/DESIGN_RULES.md F11.
CAD_ONLY_CLEARANCE_LABEL = "CAD-only assembly clearance (not simulated by design)"
CAD_ONLY_CLEARANCES = {
    "offset_rotor": "magnet pocket offset (pocket fillet = magnet_fill_radius + offset_rotor): lets a magnet be inserted at "
                    "manufacturing tolerance",
    "offset_core": "stator core fit clearance",
    "offset_tooth": "tooth fit clearance",
    "offset_bearing": "bearing seat fit clearance",
    "offset_radiator": "radiator fit clearance",
    "offset_holder": "holder fit clearance",
}

# ASSEMBLY VARIANT (owner 2026-10-15).  ``cad_variant`` is a top-level key of the configuration yaml (``L13.yaml``): ``full`` (default) builds
# the whole motor, ``frameless`` builds the kit a robot builder integrates into his own frame - stator with coils and the phase board +
# rotor with magnets - and leaves out housing, holders, rotor carriers, bearings, shaft, screws, rings and the propeller plate.  The
# build call may override it (``--variant``).  It is the same parametric model with parts omitted (docs/CAD_GENERATOR.md).
CAD_VARIANTS = ("full", "frameless")
DEFAULT_CAD_VARIANT = "full"
FRAMELESS_LABEL = "frameless kit"


def check_variant(value, where="cad_variant"):
    """The variant name (``None`` / empty -> ``full``); anything but full | frameless is an error in words."""
    if value is None or value == "":
        return DEFAULT_CAD_VARIANT
    v = str(value).strip().lower()
    if v not in CAD_VARIANTS:
        raise ValueError("%s = %r is not one of %s" % (where, value, " | ".join(CAD_VARIANTS)))
    return v


# simulation material slot -> FreeCAD body
MAT_BODIES = {"magnet": "Magnet", "stator_core": "Core", "rotor_core": "RotorCore", "slot": "Coil", "shaft": "Shaft"}
# simulation derived value -> equivalent FreeCAD expression (cross-check only)
DERIVED_EQ = {"stator_outer_radius": "stator_up_r", "stator_inner_radius": "stator_down_r",
              "rotor_outer_radius": "rotor_up_r", "rotor_inner_radius": "rotor_down_r - rotor_house_height",
              "angle_slot": "angle_wire", "angle_pole": "angle_mag", "num_slots": "num_seg * 6",
              "num_poles": "num_seg * num_poles_per_segment"}
NO_CAD = {"wire_split": "strips across the wire width (CAD draws one strip; template requires 1)",
          "wire_parallel": "parallel wires per turn - electrical only, no CAD geometry"}
FUSION_MATS = {"Core": "20SW1200", "Coil": "Copper", "RotorCore": "Steel", "Magnet": "Steel (placeholder)"}


def reference_for(die):
    """(base profile, Fusion values file) of a die: `cad/reference/<die slug>.json` (+ `<slug>_fusion_values.json`) when the
    die has its own Fusion reference (e.g. CIANO28_85_20SW1200), else the CIANO14 40 reference (the first template)."""
    sl = slug(die)
    own = os.path.join(CAD_DIR, "reference", sl + ".json")
    if os.path.exists(own):
        fv = os.path.join(CAD_DIR, "reference", sl + "_fusion_values.json")
        return own, (fv if os.path.exists(fv) else FUSION_VALUES)
    return BASE_PROFILE, FUSION_VALUES


def default_dies_dir():
    return os.environ.get("MOTRES_DIES_DIR") or (DEFAULT_SERVER_DIES if os.path.isdir(DEFAULT_SERVER_DIES)
                                                 else os.path.join(ROOT, "config", "dies"))


def default_bearings_library():
    return os.environ.get("MOTRES_BEARINGS_LIBRARY") or (
        DEFAULT_SERVER_BEARINGS if os.path.exists(DEFAULT_SERVER_BEARINGS)
        else os.path.join(ROOT, "config", "bearings_library.yaml"))


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
_FUN = dict(pi=math.pi, sqrt=math.sqrt, abs=abs, min=min, max=max, ceil=math.ceil, floor=math.floor, sin=lambda d: math.sin(math.radians(d)),
            cos=lambda d: math.cos(math.radians(d)), tan=lambda d: math.tan(math.radians(d)))


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_EXPR_CHARS = 500
_MAX_POW = 64.0


def _safe_eval(expr, ns):
    """Arithmetic on numbers, the names in ``ns`` and the ``_FUN`` functions - nothing else.  The expression comes from an
    uploaded CAD file, so it is parsed, never ``eval``-ed (a builtins-free ``eval`` is still escapable through attribute
    access; security audit 2026-10-18)."""
    if len(expr) > _MAX_EXPR_CHARS:
        raise ValueError("expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("bad expression %r" % expr) from exc

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name) and node.id in ns and not callable(ns[node.id]):
            return ns[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            a, b = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and (abs(b) > _MAX_POW or abs(a) > 1e6):
                raise ValueError("power out of range in %r" % expr)
            return _BIN[type(node.op)](a, b)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return _UN[type(node.op)](walk(node.operand))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(_FUN.get(node.func.id))
                and not node.keywords and len(node.args) <= 8):
            return _FUN[node.func.id](*[walk(a) for a in node.args])
        raise ValueError("unsupported element in expression %r" % expr)

    try:
        v = walk(tree)
    except (TypeError, OverflowError, ZeroDivisionError, RecursionError) as exc:
        raise ValueError("cannot evaluate %r: %s" % (expr, exc)) from exc
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise ValueError("expression %r is not a finite number" % expr)
    return v


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
        v = float(_safe_eval(e, ns))
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
        if k in mp["derived"] or k in mp["fusion_excluded"] or k in CAD_ONLY_CLEARANCES:
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


# ----------------------------------------------------------------------------------------------- CAD-only overrides
# Geometry tab "Full" (owner 2026-10-15): construction parameters the simulation does not read (walls, shafts, holders, rings,
# bearings, the phase board, screws, leads, CAD-only clearances) are edited per die/config and saved as the configuration's
# ``cad_overrides`` block.  They are applied to the profile AFTER the simulation rows, so they can never change an EM input;
# the catalogue of editable names lives in motor_ai_sim/cad_params.py.
CAD_OVERRIDES_KEY = "cad_overrides"
BEARING_CARD_KEY = "bearing_card"


def clean_cad_overrides(raw):
    """``{name: float}`` (+ ``bearing_card``: str) from the stored block; anything else is dropped."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        k = str(k)
        if k == BEARING_CARD_KEY:
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out[k] = float(v)
    return out


def cad_overrides_hash(raw):
    """Short id of an override set, recorded in the manifest ("none" for no overrides): a stale model is recognised by it."""
    o = clean_cad_overrides(raw)
    if not o:
        return "none"
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _override_expression(value, unit):
    return ("%g mm" % value) if unit == "mm" else ("%g deg" % value) if unit == "deg" else ("%g" % value)


def apply_cad_overrides(params, overrides, die, config):
    """Apply the numeric overrides to ``params`` ({name: profile row}) in place; returns the list of applied names.  The expression
    the row had is kept as ``reference_expression`` so an override can be reverted without the reference profile."""
    done = []
    for name, v in sorted(clean_cad_overrides(overrides).items()):
        if name == BEARING_CARD_KEY:
            continue
        p = params.get(name)
        if p is None:
            raise KeyError("cad_overrides: the CAD model has no parameter %r (die %r, config %r)" % (name, die, config))
        p.setdefault("reference_expression", p["freecad_expression"])
        p["freecad_expression"] = _override_expression(v, p.get("unit") or "")
        p["source"] = "cad_overrides %s/%s:%s" % (die, config, name)
        p["cad_override"] = True
        done.append(name)
    return done


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------------------------- the store
def list_configs(dies_dir, die):
    d = os.path.join(dies_dir, die)
    return sorted(f[:-5] for f in os.listdir(d) if f.lower().endswith(".yaml") and f != "die.yaml")


def load_effective(dies_dir, die, config, materials_library=None, bearings_library=None, cad_variant=None):
    """Effective machine of <die>/<config>: die geometry updated by the config's geometry_overrides, the config's
    materials (cards from the library) and winding.  Every input file is hashed.  ``cad_variant`` (full | frameless) is the
    configuration's top-level key of that name, unless the caller overrides it."""
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
    if cad_variant:
        variant, variant_from = check_variant(cad_variant, "--variant"), "build option"
    else:
        variant, variant_from = check_variant(cy.get("cad_variant"), "%s: cad_variant" % cfg_name), cfg_name if cy.get("cad_variant") else "default"
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
    bcards = {}
    blib = bearings_library or default_bearings_library()
    cad_ov = clean_cad_overrides(cy.get(CAD_OVERRIDES_KEY))
    used = sorted({str(v.get("card")) for k, v in (cy.get("bearings") or {}).items() if isinstance(v, dict) and v.get("card")})
    if cad_ov.get(BEARING_CARD_KEY):                           # CAD-only bearing choice: drawn after this card, the EM bearings stay
        used = sorted(set(used) | {cad_ov[BEARING_CARD_KEY]})
    if used and os.path.exists(blib):
        lib = yaml.safe_load(open(blib, encoding="utf-8")) or {}
        flat = {}
        for k, v in lib.items():
            if isinstance(v, dict):
                if any(c in v for c in used):
                    flat.update({c: v[c] for c in used if c in v})
                elif k in used:
                    flat[k] = v
        for c in used:
            if c in flat and isinstance(flat[c], dict):
                bcards[c] = {k: flat[c].get(k) for k in ("d", "D", "B", "d_m", "type", "description") if k in flat[c]}
        files["bearings_library.yaml"] = dict(sha256=sha256_file(blib), size=os.path.getsize(blib),
                                              mtime=datetime.datetime.fromtimestamp(os.path.getmtime(blib)).isoformat(timespec="seconds"),
                                              cards_used=sorted(bcards))
    return dict(die=die, config=cfg_name, variant=config_stem(config), cad_variant=variant, cad_variant_from=variant_from, geometry=geo,
                geometry_overrides=cy.get("geometry_overrides") or {}, die_geometry=dy.get("geometry") or {},
                materials=mats, winding=cy.get("winding") or {}, bearings=cy.get("bearings") or {},
                cad_overrides=cad_ov, bearing_cards=bcards, material_cards=cards, files=files, dies_dir=dies_dir)


def clearance_rows(values, reference=None):
    """The CAD-only assembly clearances among ``values`` ({name: mm}), each classified as
    ``CAD_ONLY_CLEARANCE_LABEL`` - informational rows of the review report, never a difference."""
    out = []
    for name in sorted(CAD_ONLY_CLEARANCES):
        if name in values:
            row = dict(name=name, freecad=round(values[name], 9), classification=CAD_ONLY_CLEARANCE_LABEL,
                       note=CAD_ONLY_CLEARANCES[name])
            if reference is not None and name in reference:
                row["reference"] = round(reference[name], 9)
            out.append(row)
    return out


def parameter_fingerprint(eff, mp):
    """What the CAD depends on: the primary geometry rows + materials + winding.  Same store values -> same hash."""
    rows = {k: v for k, _f, v, _u, _d in primary_rows(eff["geometry"], mp)}
    ov = clean_cad_overrides(eff.get("cad_overrides"))
    what = dict(rows=rows, materials=eff["materials"], winding=eff["winding"])
    if ov:                                                      # unchanged hash for a motor without CAD-only edits
        what["cad_overrides"] = ov
    if eff.get("cad_variant", DEFAULT_CAD_VARIANT) != DEFAULT_CAD_VARIANT:       # a full build keeps its hash (no rebuild of full models)
        what["cad_variant"] = eff["cad_variant"]
    return dict(geo_sig=geo_sig(eff["geometry"]), geo_sig_hash=geo_sig_hash(geo_sig(eff["geometry"])),
                cad_param_hash=canonical_hash(what), cad_overrides_hash=cad_overrides_hash(ov),
                cad_variant=eff.get("cad_variant", DEFAULT_CAD_VARIANT))


# ----------------------------------------------------------------------------------------------- profile (pull)
def make_profile(eff, mp, base_profile=None, fusion_values=None, stamp=None, motor_id=None):
    """Store -> (profile dict for the FreeCAD template, review report).  Where a formula of the Fusion reference
    profile already gives the simulation value it is kept (Fusion intent); any other value becomes a constant."""
    ref_base, ref_fv = reference_for(eff["die"])
    base = json.load(open(base_profile or ref_base, encoding="utf-8"))
    fus = {}
    fv = fusion_values or ref_fv
    if fv and os.path.exists(fv):
        fus = {p["name"]: p["value"] for p in json.load(open(fv, encoding="utf-8"))}
    pj = copy.deepcopy(base)
    params = {p["name"]: p for sec in SECTIONS for p in pj.get(sec, [])}
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
    bearing_rows = bearing_profile_rows(eff, params, report)
    cad_applied = apply_cad_overrides(params, eff.get("cad_overrides"), eff["die"], eff["variant"])
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
    kit = eff.get("cad_variant", DEFAULT_CAD_VARIANT) == "frameless"
    if kit:
        prof["cad_variant"] = "frameless"                    # only a frameless profile carries the key: full profiles are unchanged
    prof.update(motor_id=mid, profile_file=mid, doc_name=mid,
                variant="%s (simulation die %s, config %s)%s" % (eff["variant"], eff["die"], eff["config"], " [" + FRAMELESS_LABEL + "]" if kit else ""),
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
               parameters=report, derived_cross_check=derived, excluded=excluded, materials=mat_rows, bearings=bearing_rows,
               assembly_clearances=clearance_rows(vals), cad_overrides=dict(applied=cad_applied,
                                                                               bearing_card=(eff.get("cad_overrides") or {}).get(BEARING_CARD_KEY)),
               summary=dict(n=len(report),
                            freecad_equals_sim=all(r["status"].startswith("ok") for r in report if r.get("freecad") is not None),
                            changed_vs_fusion_reference=[r["name"] for r in report if r.get("freecad_before") is not None
                                                         and not _eq(r["freecad_before"], r["freecad"])],
                            fusion_differs_from_sim=[r["name"] for r in report if r.get("fusion") is not None
                                                     and not _eq(r["fusion"], r["sim"])],
                            sim_only=[r["name"] for r in report if r.get("freecad") is None],
                            derived_all_equal=all(d["status"] == "ok" for d in derived)))
    return pj, res


def bearing_profile_rows(eff, params, report):
    """Stage 3: bearing_d / bearing_D / bearing_B from the bearing card of the config (bearings A; B must be the same card,
    the template has one bearing type).  Without a card in the store: the standard 618/8 dimensions, stated."""
    if "bearing_d" not in params:
        return []
    bear = eff.get("bearings") or {}
    cards = [str(v.get("card")) for k, v in sorted(bear.items()) if isinstance(v, dict) and v.get("card")]
    card = cards[0] if cards else None
    chosen = (eff.get("cad_overrides") or {}).get(BEARING_CARD_KEY)
    if chosen:                                                 # CAD-only choice (Geometry > Full): the EM friction model keeps its own
        card, cards = chosen, [chosen]
    data = (eff.get("bearing_cards") or {}).get(card) if card else None
    rows = []
    if data and all(data.get(k) is not None for k in BEARING_ROWS):
        src = "simulation %s/%s bearings -> bearings_library.yaml %s" % (eff["die"], eff["config"], card)
    elif reference_for(eff["die"])[0] != BASE_PROFILE:
        # the die has its own Fusion reference (e.g. CIANO28 85: bearing 61707-2RS1, not in the bearings library): its bearing rows stay
        for key, name in BEARING_ROWS.items():
            params[name]["source"] = "die Fusion reference (no bearing card in the store%s)" % (" for %s" % card if card else "")
            rows.append(dict(card=card, field=key, name=name, value=None, expression_before=params[name]["freecad_expression"],
                             source=params[name]["source"]))
        return rows
    else:
        data, src = BEARING_618_8, "standard 618/8 dimensions (no bearing card in the store%s)" % (
            " for %s" % card if card else "")
    for key, name in BEARING_ROWS.items():
        p = params[name]
        before = p["freecad_expression"]
        p["freecad_expression"] = "%g mm" % float(data[key])
        p["source"] = src
        rows.append(dict(card=card, field=key, name=name, value=float(data[key]), expression_before=before, source=src))
    if len(set(cards)) > 1:
        rows.append(dict(warning="bearings A and B use different cards (%s); the template builds both with %s" % (cards, card)))
    return rows


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
    base_profile = base_profile or reference_for(eff["die"])[0]
    base = {p["name"]: p for sec in SECTIONS
            for p in json.load(open(base_profile, encoding="utf-8")).get(sec, [])}
    mapped = {mp["map"].get(k, k) for k in geo}
    cad_only = [dict(name=n, freecad=round(vals[n], 9), role=base.get(n, {}).get("role", "added by owner"),
                     expression=cells[n]) for n in sorted(vals) if n not in mapped and n in cells
                and base.get(n, {}).get("role") in ("input", None) and n not in BEARING_ROWS.values()
                and n not in CAD_ONLY_CLEARANCES]
    # mechanics rows (CAD only, no simulation key): differences against the CAD reference profile, target = that profile
    bvals = evaluate({n: p["freecad_expression"] for n, p in base.items()})
    mechanics = [dict(name=n, freecad=round(vals[n], 9), reference=round(bvals[n], 9), expression=cells[n],
                      target="CAD reference profile cad/reference/%s (mechanics, not in the simulation)" % os.path.basename(
                          base_profile))
                 for n in sorted(vals) if n in cells and base.get(n, {}).get("role") == "mechanics" and not _eq(vals[n], bvals[n])
                 and n not in CAD_ONLY_CLEARANCES]
    cards = (eff.get("bearing_cards") or {})
    bearing = []
    for key, name in BEARING_ROWS.items():
        if name in vals:
            card = next(iter(sorted(cards)), None)
            sim = float(cards[card][key]) if card and cards[card].get(key) is not None else float(BEARING_618_8[key])
            if not _eq(vals[name], sim):
                bearing.append(dict(name=name, freecad=round(vals[name], 9), card=card, card_value=sim,
                                    target="config bearings: choose another card in bearings_library.yaml (cards are not edited "
                                           "from CAD)"))
    res = dict(die=eff["die"], variant=eff["variant"], origin=origin, generated=stamp, store_files=eff["files"],
               compared=len(rows), equal=same, differences=diff, not_in_freecad=missing, cad_only_inputs=cad_only,
               mechanics=mechanics, bearing=bearing,
               assembly_clearances=clearance_rows({n: vals[n] for n in vals if n in cells}, bvals),
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
    if propose and (diff or mechanics):
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
        if mechanics:
            L += ["mechanics:   # CAD-only rows -> %s" % mechanics[0]["target"]]
            for d in mechanics:
                L.append("  %s: %s   # reference %s (FreeCAD expression: %s)" % (d["name"], "%g" % d["freecad"], "%g" % d["reference"],
                                                                            d["expression"]))
        if any(sec == "geometry" for (_, sec) in by_file):
            L.append("warnings:\n  - die.yaml changes affect every configuration of the die (%s)" % ", ".join(configs))
        with open(pstem + ".patch.yaml", "w", encoding="utf-8") as fh:
            fh.write("\n".join(L) + "\n")
        res["files"] += [pstem + "_fusion_params.csv", pstem + ".patch.yaml"]
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    return res
