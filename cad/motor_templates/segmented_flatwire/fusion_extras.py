"""Fusion parts the template has no geometry for (template A.7.8, CIANO28 150_35; owner 2026-10-20: the 3-D model of the 150_35 comes from Fusion).

`profile.fusion_parts.extras` lists Fusion components that exist in the owner's design but not in the parametric template (CIANO28 150: the two
fan plates "Fan 200 24s" and the 72 alumina insulation pieces "Insulation_ceramic").  `profile.fusion_parts.board` lists the components of the
Fusion phase board (8 insulation layers + 7 copper components); electronics.build_board_fusion builds the board from it.

    "extras": [{"name": "Fan_plates", "component": "Fan 200 24s", "file": "Fan_200_24s.step", "material": "Aluminium_6061",
                "occurrences": [[3 x 4], [3 x 4]], "volume_mm3": 3560.979685,   (one occurrence: the sum of the component's bodies)
                "group": "Fans", "role": "aluminium", "bom": {"key": "fan_plates", "title": "Fan plate (...)", "type": "manufactured"}}, ...]

Every solid of the component STEP is placed at every occurrence (component -> motor frame, mm) and the copies go into ONE Part::Feature
(a compound) per extra, inside the App::Part `Fusion_extras`.  Nothing is parametric: the shape is the Fusion export.  Gate: the solid count
is solids x occurrences and every occurrence carries the Fusion volume (1e-4 relative).
"""
import json

PART = "Fusion_extras"


def _cfg_dir(cfg):
    return cfg.get("dir", "")


def placed_solids(cfg, item):
    """[(occurrence index, solid in the motor frame)] of one extras / board item."""
    from . import fusion_parts as FP
    path, cands = FP.find_fusion_part(item["file"], _cfg_dir(cfg))
    if path is None:
        raise RuntimeError("Fusion STEP %s of %s not found; looked in: %s.  The part is the Fusion body (no fall-back): put the file there "
                           "or set $FC_FUSION_PARTS" % (item["file"], item.get("name") or item.get("component"), " | ".join(cands)))
    shape = FP.read_step(path)
    sols = FP.select_solids(shape, item["select"], item["file"]) if item.get("select") else list(shape.Solids)       # select: the part's own solid (a component STEP also carries its child screws)
    if FP.is_canonical(cfg, item.get("name") or item.get("component")):       # STEP round trip once (the build's STEP re-import check)
        sols = [FP._canonical(s) for s in sols]
    out = []
    for k, m12 in enumerate(item["occurrences"]):
        for s in sols:
            out.append((k, FP.transformed(s, FP.matrix(m12))))
    return out, path


def _props(f, item, path, n_solids):
    from . import fusion_parts as FP
    for prop, typ, val, tip in (
            ("SourceFile", "App::PropertyString", item["file"], "Fusion STEP the shape was read from (imported once, not parametric)"),
            ("SourceSHA256", "App::PropertyString", FP._sha256(path), "SHA-256 of that STEP"),
            ("FusionComponent", "App::PropertyString", item.get("component", ""), "Fusion component name"),
            ("FusionOccurrences", "App::PropertyInteger", len(item["occurrences"]), "occurrences of the component in the Fusion design"),
            ("FusionVolume", "App::PropertyFloat", float(item.get("volume_mm3", 0.0)), "Fusion volume of ONE occurrence (mm3), sum of its bodies"),
            ("SolidCount", "App::PropertyInteger", n_solids, "solids of the placed compound (gate)"),
            ("MaterialName", "App::PropertyString", item.get("material", ""), "material (library card) of the part"),
            ("BomKey", "App::PropertyString", (item.get("bom") or {}).get("key", ""), "bill-of-materials row key"),
            ("BomTitle", "App::PropertyString", (item.get("bom") or {}).get("title", ""), "bill-of-materials row name"),
            ("BomType", "App::PropertyString", (item.get("bom") or {}).get("type", "manufactured"), "bill-of-materials row type"),
            ("ViewGroup", "App::PropertyString", item.get("group", item.get("name", "")), "GLB group of the 3-D viewer"),
            ("ColourRole", "App::PropertyString", item.get("role", ""), "palette role (palette.py)"),
            ("SplitSides", "App::PropertyBool", bool(item.get("split_sides", False)), "GLB: one group per side (<group>_front / _back by the solid's z)"),
            ("DensityKgM3", "App::PropertyFloat", float(item.get("density_kg_m3", 0.0)), "density (kg/m3) the bill of materials weighs the part with (0 = the BOM looks the material up)"),
            ("DensitySource", "App::PropertyString", item.get("density_src", ""), "where that density comes from"),
            ("FusionMaterials", "App::PropertyString", json.dumps(item.get("fusion_materials", [])), "material names the Fusion bodies carry (JSON list; the Fusion label is not always right)"),
            ("CountSolids", "App::PropertyBool", bool(item.get("count_solids", False)), "BOM: the quantity is the number of solids (a bag of small pieces), not of occurrences"),
            ("SimplifiedRule", "App::PropertyString", item.get("simplified", ""), "SIMPLIFIED model: keep (as built) | ring (bearing ring) | board (plain plate) | omit")):
        f.addProperty(typ, prop, "Motor", tip)
        setattr(f, prop, val)


def build_extras(doc, cfg):
    """App::Part Fusion_extras with one Part::Feature (compound) per profile.fusion_parts.extras item.  Returns the report."""
    import Part
    items = cfg.get("extras") or []
    if not items:
        return None
    part = doc.addObject("App::Part", PART)
    part.Label = PART
    rep = {}
    for item in items:
        placed, path = placed_solids(cfg, item)
        f = doc.addObject("Part::Feature", item["name"])
        f.Label = item["name"]
        f.Shape = Part.makeCompound([s for _, s in placed])
        _props(f, item, path, len(placed))
        part.addObject(f)
        rep[item["name"]] = dict(step=item["file"], sha256=f.SourceSHA256, occurrences=len(item["occurrences"]), solids=len(placed),
                                 volume=round(f.Shape.Volume, 3))
    doc.recompute()
    return rep


def objects(doc):
    """The extras features (Part::Feature inside Fusion_extras), in build order."""
    part = doc.getObject(PART)
    if part is None:
        return []
    return [o for o in part.Group if o.TypeId == "Part::Feature"]


def solids(doc):
    """{name: [solids in the motor frame]} of the extras (the Part has the identity placement)."""
    return {o.Label: [s.copy() for s in o.Shape.Solids] for o in objects(doc)}


def gate(doc):
    errors = []
    for o in objects(doc):
        s = o.Shape
        if s.isNull() or len(s.Solids) != int(o.SolidCount):
            errors.append("Fusion extra %s: %d solids, the import recorded %d" % (o.Label, 0 if s.isNull() else len(s.Solids), o.SolidCount))
            continue
        if not all(sh.isClosed() for x in s.Solids for sh in x.Shells):
            errors.append("Fusion extra %s: an open shell" % o.Label)
        want = float(o.FusionVolume) * int(o.FusionOccurrences)
        if want > 0 and abs(s.Volume - want) > 1e-4 * want:
            errors.append("Fusion extra %s: volume %.3f mm3, the Fusion component gives %.3f mm3 (wrong or stale STEP)" % (o.Label, s.Volume, want))
    return errors


def bom_props(o):
    """The properties bom_dump copies into the dump (BOM rows of the extras and the Fusion board)."""
    out = {}
    for k in ("MaterialName", "BomKey", "BomTitle", "BomType", "FusionComponent", "FusionOccurrences", "FusionVolume", "SolidMaterials",
              "DensityKgM3", "DensitySource", "FusionMaterials", "SimplifiedRule", "CountSolids"):
        if hasattr(o, k):
            v = getattr(o, k)
            out[k] = json.loads(v) if k in ("SolidMaterials", "FusionMaterials") and v else v
    return out


FIT_CLEAR_MM3 = 1e-3            # a pair that Fusion keeps apart must not overlap more than this
FIT_DESIGN_TOL = 0.01           # relative: the overlap of a pair the Fusion design itself has (press fit, thread, lead through the board) vs its Fusion value
FIT_DESIGN_ABS = 0.5            # mm3


def fit(doc, cfg):
    """Interference table of an assembly whose passive parts are all Fusion extras (template A.7.9, G2).  Every pair of groups whose bounding boxes
    meet is measured (overlap volume, solid by solid); the active parts are not tested against each other (the instance check proves every copy sits on
    its Fusion occurrence).  A pair listed in ``fusion_parts.fit_design`` {"A|B": mm3} is an overlap of the Fusion design itself (bearing press fits,
    screws in tapped holes, coil leads through the phase board, coils in the back holder): reported, and it must stay at its Fusion value; every other
    pair must be clear.  Returns dict(pairs, errors)."""
    import time
    from . import health
    groups = {"Core": doc.getObject("Core").Shape, "RotorCore": doc.getObject("RotorCore").Shape}
    for lbl in ("Coils", "MagnetArray"):
        groups[lbl] = [o for o in doc.Objects if o.Label == lbl][0].Shape
    for o in objects(doc):
        groups[o.Label] = o.Shape
    design = dict(cfg.get("fit_design") or {})
    active = ("Core", "RotorCore", "Coils", "MagnetArray")
    names = list(groups)
    rows, errors, seen = [], [], set()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a in active and b in active:
                continue
            if not groups[a].BoundBox.intersect(groups[b].BoundBox):
                continue
            t0 = time.time()
            ov = health._common_volume(groups[a], groups[b])
            key = a + "|" + b if a + "|" + b in design else b + "|" + a
            kind = "design" if key in design else "clear"
            seen.add(key)
            row = dict(a=a, b=b, kind=kind, overlap_mm3=round(ov, 6), seconds=round(time.time() - t0, 1))
            if kind == "design":
                want = float(design[key])
                row["fusion_overlap_mm3"] = want
                row["ok"] = abs(ov - want) <= FIT_DESIGN_TOL * want + FIT_DESIGN_ABS
                row["note"] = "overlap of the Fusion design itself, reported: the model reproduces Fusion"
                if not row["ok"]:
                    errors.append("fit: %s / %s overlap %.3f mm3, the Fusion design has %.3f mm3 (a part is not where Fusion puts it)" % (a, b, ov, want))
            else:
                row["ok"] = ov < FIT_CLEAR_MM3
                if not row["ok"]:
                    errors.append("fit: %s runs into %s (overlap %.3f mm3; Fusion keeps them apart)" % (a, b, ov))
            rows.append(row)
    for key, want in design.items():
        if key not in seen and float(want) > FIT_DESIGN_ABS:
            errors.append("fit: the Fusion design overlap %s (%.3f mm3) is gone - the parts no longer meet" % (key, float(want)))
    return dict(pairs=rows, errors=errors, screw=None)

